"""Import one Gmail connector full-message export without logging its body."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx


# JavaScript String.trim() whitespace, including BOM but excluding Python-only controls.
JS_WHITESPACE = '\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def html_to_text(html: str) -> str:
    """Use the collector's exact ordered conversion; parity is checked against Code.gs."""
    text = re.sub(r'<(script|style)\b[^>]*>[\s\S]*?</\1>', '', html, flags=re.I | re.ASCII)
    text = re.sub(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                  lambda match: match[2] + ' (' + match[1] + ')', text, flags=re.I | re.ASCII)
    text = re.sub(r'</(p|div|li|h[1-6]|tr)>|<br[' + JS_WHITESPACE + r']*/?>', '\n', text, flags=re.I | re.ASCII)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, replacement in [('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"')]:
        text = text.replace(entity, replacement)
    text = re.sub(r'&#([0-9]+);', lambda match: chr(int(match[1])), text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip(JS_WHITESPACE)


def normalize_message(message: dict, account: str) -> dict:
    """Convert a full Gmail connector export to the Apps Script ingest contract."""
    account = account.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+', account):
        raise ValueError('A valid owning Gmail account is required')
    plain, html = [], []

    def visit(part):
        if part.get('filename'):
            return
        mime_type = part.get('mime_type')
        if mime_type in ('text/plain', 'text/html'):
            part_body = part.get('body') or {}
            content = part_body.get('content')
            if not content and part_body.get('attachment_id'):
                raise ValueError('MIME body export is incomplete; fetch the body attachment first')
            if content is not None and not isinstance(content, str):
                raise ValueError('MIME body content must be a decoded string')
            if content:
                (plain if mime_type == 'text/plain' else html).append(content)
        for child in part.get('parts') or []:
            visit(child)

    payload = message['payload']
    visit(payload)
    body = '\n\n'.join(plain) if plain else html_to_text('\n\n'.join(html))
    if not body.strip(JS_WHITESPACE):
        raise ValueError('Matching message has no readable full body')
    if len(body.encode('utf-16-le', errors='surrogatepass')) // 2 > 80000:
        raise ValueError('Matching message exceeds 80000 characters')
    subject = next((header['value'].strip(JS_WHITESPACE) for header in payload.get('headers', [])
                    if header['name'].lower() == 'subject'), '')
    internal_date = str(message['internal_date'])
    if not re.fullmatch(r'[0-9]+', internal_date):
        raise ValueError('Matching message has invalid received date')
    received = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(internal_date))
    return {'id': message['id'], 'subject': subject, 'body': body,
            'received_at': received.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'source_url': 'https://mail.google.com/mail/u/?authuser=' + quote(account, safe="~!*'()") + '#all/' + message['id']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--token-file', required=True, type=Path)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--account', required=True, help='Gmail account owning the full-message export')
    args = parser.parse_args()
    if not args.base_url.startswith('https://'):
        parser.error('HTTPS is required')
    message = json.loads(args.input.read_text(encoding='utf-8'))
    normalized = normalize_message(message, args.account)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(args.base_url.rstrip('/') + '/ingest', json={'messages': [normalized]},
            headers={'Authorization': 'Bearer ' + args.token_file.read_text().strip()})
        response.raise_for_status()
        print(json.dumps({'message_id': message['id'], 'result': response.json()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
