"""Black-box HTTP acceptance. Never import the service or print private payloads."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SHANGHAI = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 1_000_000
VIEWS = ("daily", "learning", "work", "full")
VIEW_LABELS = {"daily": "每日简报", "learning": "学习内容", "work": "工作日程", "full": "早报"}
VIEW_ENDINGS = {"daily": "本次每日简报内容已播完。", "learning": "本次学习内容已播完。",
                "work": "本次工作日程内容已播完。", "full": "本次早报内容已播完。"}
EXPECTED_STATUSES = ("ready", "disabled", "view_missing")


class AcceptanceError(Exception):
    """Only fixed, non-sensitive reason codes belong in this exception."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(condition, code):
    if not condition:
        raise AcceptanceError(code)


def iso_date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AcceptanceError("invalid_date") from None


def check_sources(sources, day):
    require(isinstance(sources, list) and 1 <= len(sources) <= 100, "missing_sources")
    identifiers = []
    for source in sources:
        require(isinstance(source, dict), "invalid_source")
        require("body" not in source, "source_body_exposed")
        sid = source.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", sid), "invalid_source_id")
        require(source.get("date") == day, "source_date_mismatch")
        identifiers.append(sid)
    require(len(set(identifiers)) == len(identifiers), "duplicate_source_ids")
    return identifiers


def check_page(body, day, section, total=None, *, view="daily"):
    require(body.get("status") == "ready", "edition_not_ready")
    require(body.get("view") == view, "view_identity_mismatch")
    require(body.get("date") == day and body.get("section") == section, "page_identity_mismatch")
    count = body.get("total_sections")
    require(type(count) is int and 1 <= count <= 32, "invalid_section_count")
    require(total is None or count == total, "edition_changed_during_check")
    require(body.get("next_section") == (section + 1 if section < count else None), "noncontinuous_pagination")
    require(isinstance(body.get("title"), str) and bool(body["title"].strip()), "missing_title")
    spoken = body.get("spoken_text")
    require(isinstance(spoken, str) and bool(spoken.strip()) and day in spoken, "missing_dated_speech")
    require(body.get("source_dates") == [day], "source_date_mismatch")
    identifiers = check_sources(body.get("sources"), day)
    try:
        generated = datetime.fromisoformat(body.get("generated_at", "").replace("Z", "+00:00"))
        require(generated.tzinfo is not None, "invalid_generated_at")
        require(generated <= datetime.now(timezone.utc) + timedelta(minutes=5), "future_generated_at")
    except (TypeError, ValueError, AttributeError):
        raise AcceptanceError("invalid_generated_at") from None
    return count, identifiers


def check_full_edition(pages, day, *, view="daily"):
    first = pages[0]
    total = first["total_sections"]
    require(len(pages) == total, "incomplete_page_collection")
    full = first.get("full_spoken_text")
    date_prefix = f"这期{VIEW_LABELS[view]}日期是{day}。"
    require(isinstance(full, str) and full.startswith(date_prefix), "missing_full_dated_speech")
    full_sources = first.get("full_sources")
    check_sources(full_sources, day)
    expected_paragraphs = [date_prefix]
    page_sources = {}
    for number, page in enumerate(pages, 1):
        require(page.get("view") == view, "view_identity_mismatch")
        section_prefix = f"第{number}段，共{total}段。"
        prefix = date_prefix + section_prefix
        spoken = page.get("spoken_text")
        require(isinstance(spoken, str) and spoken.startswith(prefix), "unexpected_page_speech_prefix")
        section_text = spoken[len(prefix):]
        require(bool(section_text.strip()), "empty_section_speech")
        expected_paragraphs.append(section_prefix + section_text)
        for source in page["sources"]:
            sid = source["id"]
            require(sid not in page_sources or page_sources[sid] == source, "source_metadata_changed")
            page_sources[sid] = source
        require(page.get("full_spoken_text") == full and page.get("full_sources") == full_sources,
                "full_edition_changed_during_check")
    expected_paragraphs.append(VIEW_ENDINGS[view])
    # Compare only page narration and the public framing contract. Warnings,
    # titles and source metadata cannot supply missing paragraphs or extra text.
    require(full == "\n\n".join(expected_paragraphs), "full_speech_content_mismatch")
    require({source["id"]: source for source in full_sources} == page_sources, "full_sources_mismatch")
    return {"characters": len(full), "sections": total, "source_count": len(page_sources)}


def check_empty_view(body, *, view, expected_status, day):
    require(body.get("status") == expected_status, "unexpected_view_status")
    require(body.get("view") == view and body.get("date") == day and body.get("section") == 1,
            "view_identity_mismatch")
    require(body.get("total_sections") == 0 and body.get("next_section") is None
            and body.get("sources") == [] and body.get("full_sources") == []
            and body.get("full_spoken_text") == "" and body.get("source_dates") == [],
            "empty_view_exposes_content")
    if expected_status == "disabled":
        require(view == "work" and body.get("title") == "" and body.get("latest_date") is None
                and not body.get("warnings") and not body.get("generated_at"), "disabled_view_exposes_metadata")
        require(body.get("spoken_text") == "工作日程尚未开启，在设置页开启后可读取。", "invalid_disabled_speech")
    else:
        require(body.get("spoken_text") == f"{day}尚无可读取的{VIEW_LABELS[view]}。", "invalid_empty_view_speech")


def verify(base_url, token, *, view="daily", expected_status="ready"):
    report = {"schema_version": 2, "started_at": datetime.now(timezone.utc).isoformat(),
              "status": "failed", "checks": [], "requests": [],
              "scope": "http_only", "view": view if view in VIEWS else None,
              "expected_status": expected_status if expected_status in EXPECTED_STATUSES else None,
              "unverified": ["gmail_provenance", "cloud_schedule", "view_source_classification_and_summary_fidelity", "qwen_tool_path", "physical_speech", "native_hook_execution"]}
    opener = build_opener(NoRedirect())

    def check(name, condition, **evidence):
        report["checks"].append({"name": name, "status": "passed" if condition else "failed", **evidence})
        require(condition, name)

    def request(label, path, *, authenticated=False, data=None):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(base_url + path, headers=headers, data=data)
        start = time.perf_counter()
        try:
            try:
                response = opener.open(req, timeout=20)
            except HTTPError as exc:
                response = exc
            with response:
                status = response.code
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            duration = round((time.perf_counter() - start) * 1000, 2)
            report["requests"].append({"name": label, "http_status": status, "duration_ms": duration})
            require(len(raw) <= MAX_RESPONSE_BYTES, "response_too_large")
            # Unauthorized and malformed requests are checked without decoding/logging bodies.
            if status != 200:
                return status, None
            try:
                body = json.loads(raw)
            except (UnicodeError, ValueError):
                raise AcceptanceError("invalid_json_response") from None
            require(isinstance(body, dict), "invalid_response_object")
            return status, body
        except (URLError, TimeoutError, OSError):
            report["requests"].append({"name": label, "error": "network_error", "duration_ms": round((time.perf_counter() - start) * 1000, 2)})
            raise AcceptanceError("network_error") from None

    def read(day, section=1):
        status, body = request("read_" + day + "_" + str(section), "/read?" + urlencode({"date": day, "section": section, "view": view}), authenticated=True)
        require(status == 200, "authenticated_read_http_status")
        return body

    try:
        require(view in VIEWS, "invalid_view")
        require(expected_status in EXPECTED_STATUSES and (expected_status != "disabled" or view == "work"), "invalid_expected_status")
        status, body = request("health", "/health")
        check("health", status == 200 and body == {"status": "ok", "service": "daily-briefing"})
        status, _ = request("unauthorized_read", "/read")
        check("unauthorized_read", status == 401)
        # Empty input cannot add a message even if scope enforcement regresses.
        status, _ = request("read_token_ingest", "/ingest", authenticated=True, data=b'{"messages":[]}')
        check("read_token_cannot_ingest", status == 401)
        status, _ = request("invalid_view", "/read?view=invalid", authenticated=True)
        check("invalid_view_rejected", status == 422)

        today = datetime.now(SHANGHAI).date().isoformat()
        first = read("latest")
        require(first.get("status") == expected_status, "unexpected_view_status")
        if expected_status != "ready":
            day = first.get("date")
            if expected_status == "view_missing":
                check("latest_date", iso_date(day) <= iso_date(today))
            else:
                require(day is None, "disabled_view_exposes_metadata")
            check_empty_view(first, view=view, expected_status=expected_status, day=day)
            check("selected_view_" + expected_status, True)
            current = read("today")
            require(datetime.now(SHANGHAI).date().isoformat() == today, "date_rolled_over_rerun")
            if expected_status == "view_missing" and day < today:
                require(current.get("status") == "missing" and current.get("view") == view
                        and current.get("date") == today and current.get("total_sections") == 0
                        and current.get("sources") == [] and current.get("full_sources") == []
                        and current.get("full_spoken_text") == "" and current.get("next_section") is None
                        and current.get("latest_date") == day, "today_does_not_substitute_older_edition")
            else:
                check_empty_view(current, view=view, expected_status=expected_status, day=today)
            check("today_" + expected_status, True)
            report["unverified"].append("selected_view_content_and_pagination")
            if expected_status == "view_missing":
                report["limitation"] = "selected_view_content_unavailable"
        else:
            verify_ready(first, read, check, report, today, view)
        durations = [r["duration_ms"] for r in report["requests"] if r["name"].startswith("read_") and r.get("http_status") == 200]
        max_ms = max(durations)
        within = max_ms <= 3000
        report["checks"].append({"name": "http_read_latency", "status": "passed" if within else "limited",
                                 "maximum_ms": max_ms, "threshold_ms": 3000, "samples": len(durations),
                                 "measurement": "client_end_to_end_not_qwen"})
        report["status"] = "passed" if within and expected_status != "view_missing" else "limited"
        if not within:
            report["limitation"] = "observed_network_path_exceeds_qwen_3000ms_budget"
    except AcceptanceError as exc:
        report["error"] = str(exc)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def verify_ready(first, read, check, report, today, view):
    day = first.get("date")
    check("latest_date", iso_date(day) <= iso_date(today))
    total, ids = check_page(first, day, 1, view=view)
    check("latest_edition", True, date=day, total_sections=total)
    pages = [first]
    for number in range(2, total + 1):
        page = read(day, number)
        _, page_ids = check_page(page, day, number, total, view=view)
        require(page.get("generated_at") == first.get("generated_at"), "edition_changed_during_check")
        ids.extend(page_ids)
        pages.append(page)
    full_evidence = check_full_edition(pages, day, view=view)
    check("full_edition_content_and_sources", True, **full_evidence)
    terminal = read(day, total + 1)
    check("pagination_and_terminal", terminal.get("status") == "finished" and terminal.get("view") == view and terminal.get("date") == day
          and terminal.get("section") == total + 1 and terminal.get("total_sections") == total
          and terminal.get("next_section") is None and terminal.get("sources") == [], checked_pages=total)
    unique_ids = sorted(set(ids))
    check("source_ids", bool(unique_ids), count=len(unique_ids),
          sha256=hashlib.sha256(json.dumps(unique_ids, separators=(",", ":")).encode()).hexdigest())

    current = read("today")
    require(datetime.now(SHANGHAI).date().isoformat() == today, "date_rolled_over_rerun")
    check("today_date", current.get("date") == today)
    if day < today:
        check("today_does_not_substitute_older_edition", current.get("status") == "missing" and current.get("view") == view
              and current.get("total_sections") == 0 and current.get("sources") == []
              and current.get("full_spoken_text") == "" and current.get("full_sources") == []
              and current.get("next_section") is None and current.get("latest_date") == day,
              expected_status="missing", latest_date=day)
    else:
        check_page(current, today, 1, view=view)
        require(current.get("generated_at") == first.get("generated_at")
                and current.get("full_spoken_text") == first.get("full_spoken_text")
                and current.get("full_sources") == first.get("full_sources"), "edition_changed_during_check")
        check("today_ready", True)
        report["unverified"].append("live_missing_today_branch_requires_older_only_dataset")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="HTTPS origin or URL ending in /briefing/v1")
    parser.add_argument("--read-token-file", required=True, type=Path, help="UTF-8 file containing only the read token")
    parser.add_argument("--view", choices=VIEWS, default="daily", help="Prepared view to verify (default: daily)")
    parser.add_argument("--expect-status", choices=EXPECTED_STATUSES, default="ready",
                        help="Explicit empty-state contract; view_missing remains limited, never accepted as prepared content")
    parser.add_argument("--output", type=Path, help="Optional JSON evidence path under an ignored runtime directory")
    args = parser.parse_args()
    try:
        base = args.base_url.rstrip("/")
        url = urlsplit(base)
        require(bool(url.hostname) and not (url.username or url.password or url.query or url.fragment), "invalid_base_url")
        require(url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}), "https_required")
        require(url.path in {"", "/briefing/v1"}, "invalid_api_prefix")
        if not url.path:
            base += "/briefing/v1"
        token = args.read_token_file.read_text(encoding="utf-8-sig").strip()
        require(len(token) >= 24 and token.isascii() and not any(c.isspace() for c in token), "invalid_token_file")
        report = verify(base, token, view=args.view, expected_status=args.expect_status)
    except (OSError, UnicodeError):
        report = {"schema_version": 2, "status": "failed", "view": args.view, "expected_status": args.expect_status, "error": "token_file_unreadable"}
    except (AcceptanceError, ValueError) as exc:
        report = {"schema_version": 2, "status": "failed", "view": args.view, "expected_status": args.expect_status, "error": str(exc) if isinstance(exc, AcceptanceError) else "invalid_base_url"}
    output = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    return {"passed": 0, "failed": 1, "limited": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
