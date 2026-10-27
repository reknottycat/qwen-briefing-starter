"""Synthetic connector exports: no private mail or runtime fixtures are used."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from briefing.models import Ingest
from briefing.store import Store
from scripts.ingest_saved_email import normalize_message

ROOT = Path(__file__).resolve().parents[1]
TEST_ACCOUNT = 'you@example.com'


def connector_message(payload):
    return {'id': 'synthetic001', 'thread_id': 'synthetic_thread', 'internal_date': '1793030400123',
            'snippet': 'A snippet is not the full body', 'payload': {
                'headers': [{'name': 'Subject', 'value': ' \ufeff[每日早报][每日资讯][2026-10-26] \r\n'}],
                **payload}}


def text_part(content, mime='text/plain', **fields):
    return {'mime_type': mime, 'filename': '', 'body': {'content': content, 'size': len(content.encode())}, **fields}


def collector_normalize(message):
    """Run the actual collector on the equivalent Gmail tree, without Google services."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for cross-runtime collector parity')
    script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const message = JSON.parse(fs.readFileSync(0, 'utf8'));
function part(source) {
  const body = source.body || {};
  return {mimeType: source.mime_type, filename: source.filename, headers: source.headers,
    body: {data: typeof body.content === 'string' ? Buffer.from(body.content).toString('base64url') : undefined},
    parts: (source.parts || []).map(part)};
}
const context = vm.createContext({Utilities: {
  base64Decode: value => Buffer.from(value, 'base64'),
  newBlob: bytes => ({getDataAsString: () => Buffer.from(bytes).toString('utf8')})
}, console: {log() {}}});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const payload = part(message.payload);
const normalized = {id: message.id, subject: context.header_(payload, 'Subject'),
  body: context.body_(payload, message.id), received_at: new Date(Number(message.internal_date)).toISOString(),
  source_url: 'https://mail.google.com/mail/u/?authuser=' +
    encodeURIComponent(process.argv[2]) + '#all/' + message.id};
process.stdout.write(JSON.stringify(normalized));
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'cloud-gmail' / 'Code.gs'), TEST_ACCOUNT],
                            input=json.dumps(message, ensure_ascii=False), encoding='utf-8',
                            capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize('payload', [
    text_part('早报事件类型：定时结果\n  完整纯文本。保留空白和结尾换行。\n'),
    text_part('\ufeff<p>早报事件类型：定时结果</p><style>隐藏样式</style><script>隐藏脚本</script><h4>标题</h4><div>价格&nbsp;100元。</div>'
              '<p><a href="https://example.com/a?x=1&amp;y=2"><b>原始来源</b></a><br>仍需确认。</p>'
              '<table><tr><td>表格行</td></tr></table>&amp;lt;&quot;&#20013;&#128512;\n\n\n  ', 'text/html'),
    {'mime_type': 'multipart/alternative', 'parts': [text_part('早报事件类型：定时结果\n原文第一部分\n'), text_part('原文第二部分'),
        text_part('<p>替代HTML不重复收录</p>', 'text/html'), text_part('附件不收录', filename='notes.txt')]},
    {'mime_type': 'multipart/mixed', 'parts': [text_part('<p>早报事件类型：定时结果</p><p>第一块</p>', 'text/html'),
        text_part('<p>第二块 <a href="http://example.com/source">来源</a></p>', 'text/html'),
        text_part('非文本MIME不收录', 'application/json')]},
])
def test_saved_connector_matches_real_collector_and_ingests_as_duplicate(tmp_path, payload):
    message = connector_message(payload)
    actual = normalize_message(message, TEST_ACCOUNT)
    expected = collector_normalize(message)
    assert actual == expected
    assert actual['received_at'] == '2026-10-26T16:00:00.123Z'
    store = Store(tmp_path, clock=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert store.ingest(Ingest(messages=[expected]))['accepted'] == 1
    assert store.ingest(Ingest(messages=[actual])) == {'status': 'accepted', 'accepted': 0, 'duplicates': 1}


def test_missing_separate_text_body_is_not_silently_dropped():
    message = connector_message({'mime_type': 'multipart/mixed', 'parts': [text_part('已有一段'),
        {'mime_type': 'text/plain', 'body': {'attachment_id': 'synthetic-body', 'size': 100}}]})
    with pytest.raises(ValueError, match='export is incomplete'):
        normalize_message(message, TEST_ACCOUNT)


@pytest.mark.parametrize('body,reason', [('', 'no readable full body'), ('\ufeff \n', 'no readable full body'),
                                      ('😀' * 40001, 'exceeds 80000 characters')], ids=['empty', 'whitespace', 'oversized'])
def test_empty_or_oversized_body_is_not_imported(body, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_message(connector_message(text_part(body)), TEST_ACCOUNT)


def test_plaintext_boundary_is_utf16_units_like_collector():
    body = '😀' * 40000
    assert normalize_message(connector_message(text_part(body)), TEST_ACCOUNT)['body'] == body


def test_normalization_does_not_mutate_the_connector_export():
    message = connector_message(text_part('<p>合成测试正文</p>', 'text/html'))
    before = deepcopy(message)
    normalize_message(message, TEST_ACCOUNT)
    assert message == before


@pytest.mark.parametrize('date', ['not-a-date', '', '-1', '1.5', True])
def test_invalid_received_date_is_not_coerced(date):
    message = connector_message(text_part('合成测试正文'))
    message['internal_date'] = date
    with pytest.raises(ValueError, match='invalid received date'):
        normalize_message(message, TEST_ACCOUNT)


@pytest.mark.parametrize('subject', ['Internal Sample 项目日报｜2026-10-26',
    '[每日早报][Internal Sample 项目日报][2026-10-26]'])
def test_bootstrap_export_cannot_bypass_excluded_report_source(tmp_path, subject):
    message = connector_message(text_part('仅用于拒收测试的合成正文。'))
    message['payload']['headers'][0]['value'] = subject
    normalized = normalize_message(message, TEST_ACCOUNT)
    store = Store(tmp_path)
    with pytest.raises(ValidationError, match='report_subject_not_allowed'):
        store.ingest(Ingest(messages=[normalized]))
    assert store.state['messages'] == {}


def test_saved_export_uses_explicit_owner_in_source_link():
    actual = normalize_message(connector_message(text_part('合成正文')), ' Other+Reports@Example.com ')
    assert actual['source_url'] == 'https://mail.google.com/mail/u/?authuser=other%2Breports%40example.com#all/synthetic001'


@pytest.mark.parametrize('account', ['', 'person', 'person @example.com'])
def test_saved_export_rejects_missing_or_invalid_owner(account):
    with pytest.raises(ValueError, match='owning Gmail account'):
        normalize_message(connector_message(text_part('合成正文')), account)
