"""Exercise the independent verifier through an isolated HTTP contract server."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import verify_live


TOKEN = "isolated-test-read-token-123456789"
GENERATED_AT = datetime.now(timezone.utc).isoformat()
FRAMING = {
    "daily": ("每日简报", "本次每日简报内容已播完。"),
    "learning": ("学习内容", "本次学习内容已播完。"),
    "work": ("工作日程", "本次工作日程内容已播完。"),
    "full": ("早报", "本次早报内容已播完。"),
}


def page(view, day, section=1, status="ready"):
    label, ending = FRAMING[view]
    result = {
        "status": status, "date": day, "view": view, "section": section,
        "topic": "all", "title": "隔离验收样稿", "total_sections": 2,
        "source_dates": [day], "generated_at": GENERATED_AT,
        "warnings": [], "latest_date": day,
    }
    sources = [
        {"id": "example_a", "subject": "合成甲", "date": day, "source_url": None},
        {"id": "example_b", "subject": "合成乙", "date": day, "source_url": None},
    ]
    prefix = f"这期{label}日期是{day}。"
    result["full_spoken_text"] = "\n\n".join([
        prefix, "第1段，共2段。第一段的独立合成正文。", "第2段，共2段。第二段的独立合成正文。", ending,
    ])
    result["full_sources"] = sources
    result["sources"] = [sources[section - 1]] if section <= 2 else []
    result["next_section"] = 2 if section == 1 else None
    result["spoken_text"] = prefix + (
        "第1段，共2段。第一段的独立合成正文。" if section == 1 else "第2段，共2段。第二段的独立合成正文。"
    )
    if section > 2:
        result.update(status="finished", spoken_text=f"{day}的{label}已经播完。")
    if status in {"disabled", "view_missing"}:
        result.update(title="", total_sections=0, source_dates=[], sources=[], full_sources=[],
                      full_spoken_text="", next_section=None)
        if status == "disabled":
            result.update(spoken_text="工作日程尚未开启，在设置页开启后可读取。", latest_date=None)
            result.pop("generated_at")
        else:
            result["spoken_text"] = f"{day}尚无可读取的{label}。"
    return result


@contextmanager
def contract_server(*, status="ready", mutate=None, reject_invalid=True):
    requests = []
    today = datetime.now(verify_live.SHANGHAI).date().isoformat()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.handle_contract()

        def do_GET(self):
            self.handle_contract()

        def handle_contract(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            requests.append((self.command, url.path, query))
            response = None
            code = 200
            if url.path.endswith("/health"):
                response = {"status": "ok", "service": "daily-briefing"}
            elif (self.headers.get("Authorization") != "Bearer " + TOKEN
                  or url.path.endswith("/ingest")):
                code = 401
            elif query.get("view") == ["invalid"] and reject_invalid:
                code = 422
            else:
                view = query.get("view", ["daily"])[0]
                if view == "invalid":
                    view = "daily"
                requested_day = query.get("date", ["today"])[0]
                day = today if requested_day in {"today", "latest"} else requested_day
                if status == "disabled" and requested_day == "latest":
                    day = None
                response = page(view, day, int(query.get("section", [1])[0]), status)
                if mutate:
                    mutate(response, requested_day)
            raw = json.dumps(response or {"detail": "deliberately omitted"}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    server_thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/briefing/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.parametrize("view", ["daily", "learning", "work", "full"])
def test_ready_views_are_checked_over_actual_http(view):
    with contract_server() as (base, calls):
        result = verify_live.verify(base, TOKEN, view=view)
    assert result["status"] == "passed", result
    assert result["view"] == view
    assert result["expected_status"] == "ready"
    assert all(call[2]["view"] == [view] for call in calls
               if call[1].endswith("/read") and "date" in call[2])
    assert {check["name"] for check in result["checks"]} >= {
        "invalid_view_rejected", "full_edition_content_and_sources", "pagination_and_terminal", "today_ready",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert TOKEN not in serialized and "独立合成正文" not in serialized and "example_a" not in serialized


@pytest.mark.parametrize("mutation,error", [
    (lambda body, _day: body.update(view="full"), "view_identity_mismatch"),
    (lambda body, _day: body.update(full_spoken_text=body["full_spoken_text"].replace("第二段的独立合成正文。", "")),
     "full_speech_content_mismatch"),
    (lambda body, _day: body["full_sources"].append(deepcopy(body["full_sources"][0])), "duplicate_source_ids"),
    (lambda body, _day: body["sources"][0].update(body="private-original") if body["sources"] else None,
     "source_body_exposed"),
])
def test_wrong_view_missing_paragraph_and_source_leaks_fail(mutation, error):
    with contract_server(mutate=mutation) as (base, _):
        result = verify_live.verify(base, TOKEN)
    assert result["status"] == "failed"
    assert result["error"] == error
    assert "private-original" not in json.dumps(result)


def test_disabled_work_requires_explicit_expectation():
    with contract_server(status="disabled") as (base, _):
        ordinary = verify_live.verify(base, TOKEN, view="work")
        explicit = verify_live.verify(base, TOKEN, view="work", expected_status="disabled")
    assert ordinary["status"] == "failed"
    assert ordinary["error"] == "unexpected_view_status"
    assert explicit["status"] == "passed", explicit
    assert "selected_view_content_and_pagination" in explicit["unverified"]


def test_disabled_work_must_not_expose_even_prior_metadata():
    with contract_server(status="disabled", mutate=lambda body, _: body.update(title="private prior title")) as (base, _):
        result = verify_live.verify(base, TOKEN, view="work", expected_status="disabled")
    assert result["status"] == "failed"
    assert result["error"] == "disabled_view_exposes_metadata"


@pytest.mark.parametrize("view", ["learning", "work"])
def test_view_missing_never_passes_as_prepared_content(view):
    with contract_server(status="view_missing") as (base, _):
        ordinary = verify_live.verify(base, TOKEN, view=view)
        explicit = verify_live.verify(base, TOKEN, view=view, expected_status="view_missing")
    assert ordinary["status"] == "failed"
    assert explicit["status"] == "limited", explicit
    assert explicit["limitation"] == "selected_view_content_unavailable"
    assert "selected_view_content_and_pagination" in explicit["unverified"]


def test_server_must_reject_unknown_view_instead_of_falling_back():
    with contract_server(reject_invalid=False) as (base, _):
        result = verify_live.verify(base, TOKEN)
    assert result["status"] == "failed"
    assert result["error"] == "invalid_view_rejected"


def test_invalid_local_options_do_not_make_a_request():
    with contract_server() as (base, calls):
        unknown = verify_live.verify(base, TOKEN, view="unknown-sensitive-input")
        wrong_disabled = verify_live.verify(base, TOKEN, view="learning", expected_status="disabled")
    assert unknown["error"] == "invalid_view" and unknown["view"] is None
    assert wrong_disabled["error"] == "invalid_expected_status"
    assert calls == []


def test_more_than_three_seconds_is_limited(monkeypatch):
    tick = iter(number * 3.1 for number in range(100))
    monkeypatch.setattr(verify_live.time, "perf_counter", lambda: next(tick))
    with contract_server() as (base, _):
        result = verify_live.verify(base, TOKEN, view="learning")
    assert result["status"] == "limited"
    assert result["limitation"] == "observed_network_path_exceeds_qwen_3000ms_budget"
    latency = next(check for check in result["checks"] if check["name"] == "http_read_latency")
    assert latency["threshold_ms"] == 3000 and latency["maximum_ms"] == 3100
