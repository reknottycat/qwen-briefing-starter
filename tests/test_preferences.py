"""Synthetic management, persistence and generation-boundary regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from briefing.api import PREFIX, create_app
import briefing.preferences as preferences_module
from briefing.source_policy import SOURCE_POLICY

ORIGIN = "https://briefing.test"
TODAY = "2026-10-27"
YESTERDAY = "2026-10-26"
NOW = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)
TOKENS = {role: role + "-" + "x" * 32 for role in ("read", "worker", "ingest", "manage")}
MANAGE = PREFIX + "/manage"
STATUS_FIELDS = {"date", "source_count", "status", "preferences_revision",
                 "edition_preferences_revision", "generated_at", "error"}


def auth(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


@pytest.fixture
def setup(tmp_path):
    current = [NOW]
    app = create_app(tmp_path, TOKENS, clock=lambda: current[0])
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, app.state.store, current


def effective(client, headers=None):
    response = client.get(MANAGE + "/preferences", headers=auth("manage") if headers is None else headers)
    assert response.status_code == 200, response.text
    return response.json()


def update_payload(preferences):
    return {"expected_revision": preferences["revision"], "work_enabled": preferences["work_enabled"], "sources": [
        {key: source[key] for key in ("name", "category", "mode", "narration_detail", "note")}
        for source in preferences["sources"]
    ]}


def save(client, payload, headers=None):
    return client.put(MANAGE + "/preferences", json=payload,
                      headers=auth("manage") if headers is None else headers)


def status(client):
    response = client.get(MANAGE + "/status", headers=auth("manage"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert STATUS_FIELDS <= set(result)
    assert result["date"] == TODAY
    return result


def message(message_id="mail-today", day=TODAY):
    return {"id": message_id, "subject": f"[每日早报][每日资讯][{day}]",
            "received_at": "2026-10-27T00:30:00Z", "body": "早报事件类型：定时结果\n合成资料，尚待核实。",
            "source_url": "https://example.com/synthetic-report"}


def ingest(client, *messages):
    response = client.post(PREFIX + "/ingest", headers=auth("ingest"), json={"messages": list(messages)})
    assert response.status_code == 200, response.text
    return response.json()


def next_job(client):
    response = client.get(PREFIX + "/jobs/next", headers=auth("worker"))
    assert response.status_code == 200, response.text
    return response.json()


def edition(job, text="甲：合成结论。\n乙：保留尚待核实的条件。"):
    return {"date": job["date"], "title": "合成对话早报", "format": "mixed",
            "preferences_revision": job["preferences"]["revision"], "generated_at": NOW.isoformat(),
            "source_message_ids": sorted(m["id"] for m in job["messages"]),
            "source_dates": sorted({m["date"] for m in job["messages"]}), "fingerprint": job["fingerprint"],
            "rebuild_id": job.get("rebuild_id"), "lease_id": job.get("lease_id"),
            "sections": [{"title": "合成来源", "topic": "进展", "text": text, "source_ids": [m["id"]]}
                         for m in job["messages"]], "warnings": []}


def publish(client, payload):
    return client.post(PREFIX + "/editions", headers=auth("worker"), json=payload)


def failure(job):
    return {"date": job["date"], "fingerprint": job["fingerprint"], "code": "codex_failed",
            "rebuild_id": job.get("rebuild_id"), "lease_id": job.get("lease_id"),
            "preferences_revision": job["preferences"]["revision"]}


def prepare(client, day, message_id):
    ingest(client, message(message_id, day))
    job = next_job(client)
    assert job["status"] == "ready" and job["date"] == day
    response = publish(client, edition(job))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"


def login(client, token=None, origin=ORIGIN):
    return client.post(MANAGE + "/session", json={"token": TOKENS["manage"] if token is None else token},
                       headers={"Origin": origin} if origin is not None else {})


def assert_private_headers(response, management=False):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    if management:
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        csp = response.headers["content-security-policy"]
        for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'",
                          "connect-src 'self'", "img-src 'self' data:", "base-uri 'none'",
                          "frame-ancestors 'none'", "form-action 'self'"):
            assert directive in csp


def test_default_preferences_expose_only_canonical_identity_and_editable_settings(setup):
    client, _, _ = setup
    actual = effective(client)
    assert actual["revision"] == 1
    assert actual["format"] == "mixed"
    assert actual["work_enabled"] is False
    defaults = preferences_module.default_preferences()
    learning = {"每日知识练习", "外语练习", "技术周报", "每周学习复盘"}
    assert actual["sources"] == [
        {"name": source["name"], "kind": source["kind"], "event_type": source["event_type"],
         "order": index, "category": "learning" if source["name"] in learning else "news",
         "mode": "processed", "narration_detail": defaults["sources"][source["name"]]["narration_detail"], "note": ""}
        for index, source in enumerate(SOURCE_POLICY["sources"])
    ]
    assert len(actual["sources"]) == 10
    assert status(client) == {"date": TODAY, "source_count": 0, "status": "no_sources", "preferences_revision": 1,
                              "edition_preferences_revision": None, "generated_at": None, "error": None}


@pytest.mark.parametrize("method,path", [("GET", "/preferences"), ("GET", "/status"), ("PUT", "/preferences")])
def test_management_rejects_missing_and_legacy_credentials(setup, method, path):
    client, _, _ = setup
    payload = update_payload(effective(client)) if method == "PUT" else None
    for role in (None, "read", "worker", "ingest"):
        headers = auth(role) if role else {}
        response = client.request(method, MANAGE + path, headers=headers, json=payload)
        assert response.status_code == 401
        assert_private_headers(response, management=True)


@pytest.mark.parametrize("method,path", [("GET", "/read"), ("GET", "/jobs/next"), ("POST", "/ingest"),
                                         ("POST", "/editions"), ("POST", "/jobs/failure"), ("POST", "/jobs/rebuild")])
def test_manage_credential_cannot_use_legacy_scopes(setup, method, path):
    client, _, _ = setup
    response = client.request(method, PREFIX + path, headers=auth("manage"))
    assert response.status_code == 401


def test_management_is_disabled_without_its_token_while_legacy_routes_work(tmp_path):
    legacy = {role: TOKENS[role] for role in ("read", "worker", "ingest")}
    app = create_app(tmp_path, legacy, clock=lambda: NOW)
    with TestClient(app, base_url=ORIGIN) as client:
        for path in ("/preferences", "/status"):
            response = client.get(MANAGE + path, headers=auth("manage"))
            assert response.status_code == 503
            assert response.json()["detail"] == "management_disabled"
        assert login(client).status_code == 503
        assert client.get(PREFIX + "/read", headers=auth("read")).json()["status"] == "missing"
        ingest(client, message())
        job = next_job(client)
        assert job["status"] == "ready"
        assert publish(client, edition(job)).status_code == 200
        assert client.get(PREFIX + "/read", headers=auth("read")).json()["status"] == "ready"


@pytest.mark.parametrize("manage_token", [TOKENS["read"], TOKENS["worker"], TOKENS["ingest"], "short"])
def test_management_token_must_be_long_and_separate_from_existing_scopes(tmp_path, manage_token):
    with pytest.raises(RuntimeError):
        create_app(tmp_path, {**TOKENS, "manage": manage_token}, clock=lambda: NOW)


@pytest.mark.parametrize("invalid", ["detail", "mode", "missing_mode", "category", "missing_category", "unknown", "duplicate",
                                     "missing_source", "long_note", "null_note", "missing_note", "missing_detail", "kind", "event_type",
                                     "order", "format", "bool_revision", "missing_work_enabled", "invalid_work_enabled"])
def test_invalid_preferences_leave_saved_settings_and_queue_unchanged(setup, invalid):
    client, store, _ = setup
    before = effective(client)
    payload = update_payload(before)
    first = payload["sources"][0]
    if invalid == "detail":
        first["narration_detail"] = "verbose"
    elif invalid == "mode":
        first["mode"] = "summary"
    elif invalid == "missing_mode":
        del first["mode"]
    elif invalid == "category":
        first["category"] = "business"
    elif invalid == "missing_category":
        del first["category"]
    elif invalid == "unknown":
        first["name"] = "未选择的合成来源"
    elif invalid == "duplicate":
        payload["sources"][1] = deepcopy(first)
    elif invalid == "missing_source":
        payload["sources"].pop()
    elif invalid == "long_note":
        first["note"] = "私" * 201
    elif invalid == "null_note":
        first["note"] = None
    elif invalid == "missing_note":
        del first["note"]
    elif invalid == "missing_detail":
        del first["narration_detail"]
    elif invalid in ("kind", "event_type", "order"):
        first[invalid] = {"kind": "weekly", "event_type": "新回复", "order": 9}[invalid]
    elif invalid == "format":
        payload["format"] = "dialogue"
    elif invalid == "missing_work_enabled":
        del payload["work_enabled"]
    elif invalid == "invalid_work_enabled":
        payload["work_enabled"] = "false"
    else:
        payload["expected_revision"] = True
    state_before = deepcopy(store.state)
    response = save(client, payload)
    assert response.status_code == 422
    assert "私" * 201 not in response.text
    assert effective(client) == before
    assert store.state == state_before


@pytest.mark.parametrize("mode", ["raw", "processed", "podcast"])
def test_all_supported_modes_round_trip_without_changing_source_identity(setup, mode):
    client, _, _ = setup
    before = effective(client)
    payload = update_payload(before)
    payload["sources"][0]["mode"] = mode
    response = save(client, payload)
    assert response.status_code == 200
    actual = response.json()
    assert actual["format"] == "mixed"
    assert actual["sources"][0] == {**before["sources"][0], "mode": mode}
    assert actual["sources"][1:] == before["sources"][1:]
    assert actual["changed"] is (mode != "processed")


@pytest.mark.parametrize("missing", [True, False], ids=["missing-mode", "invalid-mode"])
def test_shared_snapshot_requires_valid_mode_for_every_source(missing):
    snapshot = preferences_module.default_preferences()
    last = snapshot["sources"][SOURCE_POLICY["sources"][-1]["name"]]
    if missing:
        del last["mode"]
    else:
        last["mode"] = "summary"
    with pytest.raises(ValueError):
        preferences_module.validate_preferences_snapshot(snapshot)


def test_note_boundary_empty_note_and_unchanged_save_do_not_raise_revision(setup):
    client, _, _ = setup
    initial = effective(client)
    payload = update_payload(initial)
    payload["sources"][0]["note"] = " " + "备" * 198 + " "
    saved = save(client, payload).json()
    assert saved["revision"] == initial["revision"] + 1 and saved["changed"] is True
    assert saved["sources"][0]["note"] == payload["sources"][0]["note"]
    unchanged = save(client, update_payload(saved)).json()
    assert unchanged["revision"] == saved["revision"] and unchanged["changed"] is False
    clear = update_payload(saved)
    clear["sources"][0]["note"] = ""
    cleared = save(client, clear).json()
    assert cleared["revision"] == saved["revision"] + 1
    assert cleared["sources"][0]["note"] == ""


def test_stale_expected_revision_cannot_overwrite_preferences_or_queue(setup):
    client, store, _ = setup
    stale = update_payload(effective(client))
    changed = deepcopy(stale)
    changed["sources"][0]["note"] = "只保留原文已有的结论。"
    assert save(client, changed).status_code == 200
    before = effective(client)
    state_before = deepcopy(store.state)
    stale["sources"][0]["narration_detail"] = "simple"
    assert save(client, stale).status_code == 409
    assert effective(client) == before
    assert store.state == state_before


def test_saved_preferences_survive_restart_and_are_not_replaced_by_new_defaults(setup, monkeypatch):
    client, store, current = setup
    payload = update_payload(effective(client))
    payload["work_enabled"] = True
    payload["sources"][0].update(category="work", narration_detail="simple", note="保留这条已保存偏好。")
    assert save(client, payload).status_code == 200
    expected = effective(client)
    changed_policy = deepcopy(SOURCE_POLICY)
    changed_policy["sources"][0]["narration_detail"] = "detailed"
    monkeypatch.setattr(preferences_module, "SOURCE_POLICY", changed_policy)
    restarted = create_app(store.directory, TOKENS, clock=lambda: current[0])
    with TestClient(restarted, base_url=ORIGIN) as after_restart:
        assert effective(after_restart) == expected
        assert status(after_restart)["preferences_revision"] == expected["revision"]


def test_saving_without_today_sources_never_enqueues_or_rebuilds_history(setup):
    client, store, _ = setup
    prepare(client, YESTERDAY, "historical")
    history = deepcopy(store.state["editions"][YESTERDAY])
    payload = update_payload(effective(client))
    payload["sources"][0]["note"] = "仅影响后续生成。"
    response = save(client, payload)
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is True
    assert result["generation"]["queue_status"] == "no_sources"
    assert result["generation"]["status"] == "no_sources"
    assert result["generation"]["source_count"] == 0
    assert STATUS_FIELDS <= set(result["generation"])
    assert store.state["jobs"] == {}
    assert store.state["editions"][YESTERDAY] == history
    assert next_job(client) == {"status": "idle"}


def test_changed_preferences_queue_only_today_keep_old_editions_and_publish_new_revision(setup):
    client, store, _ = setup
    prepare(client, YESTERDAY, "historical")
    prepare(client, TODAY, "current")
    editions_before = deepcopy(store.state["editions"])
    old_read = client.get(PREFIX + "/read", headers=auth("read")).json()["full_spoken_text"]
    payload = update_payload(effective(client))
    payload["sources"][0].update(narration_detail="simple", note="使用原文重点。")
    response = save(client, payload)
    assert response.status_code == 200
    saved = response.json()
    assert saved["changed"] is True
    assert saved["generation"]["queue_status"] == "queued"
    assert status(client)["status"] == "queued"
    assert status(client)["edition_preferences_revision"] == 1
    assert set(store.state["jobs"]) == {TODAY}
    assert store.state["editions"] == editions_before
    assert client.get(PREFIX + "/read", headers=auth("read")).json()["full_spoken_text"] == old_read
    job = next_job(client)
    assert job["date"] == TODAY
    assert job["preferences"] == {"revision": saved["revision"], "format": "mixed", "work_enabled": saved["work_enabled"], "sources": {
        source["name"]: {"category": source["category"], "mode": source["mode"], "narration_detail": source["narration_detail"], "note": source["note"]}
        for source in saved["sources"]
    }}
    assert status(client)["status"] == "generating"
    assert publish(client, edition(job, "甲：新合成稿。\n乙：必要条件仍保留。")).status_code == 200
    ready = status(client)
    assert ready["status"] == "ready"
    assert ready["preferences_revision"] == ready["edition_preferences_revision"] == saved["revision"]
    assert ready["generated_at"] and ready["error"] is None
    assert store.state["editions"][YESTERDAY] == editions_before[YESTERDAY]
    assert store.state["history"][TODAY][-1] == editions_before[TODAY]


def test_identical_save_queues_today_once_and_does_not_interrupt_an_active_lease(setup):
    client, store, _ = setup
    prepare(client, TODAY, "current")
    current_preferences = effective(client)
    payload = update_payload(current_preferences)
    first = save(client, payload).json()
    assert first["revision"] == current_preferences["revision"] and first["changed"] is False
    assert first["generation"]["queue_status"] == "queued"
    queued = deepcopy(store.state["jobs"][TODAY])
    repeated = save(client, payload).json()
    assert repeated["generation"]["queue_status"] == "already_queued"
    assert store.state["jobs"][TODAY] == queued
    leased = next_job(client)
    active = deepcopy(store.state["jobs"][TODAY])
    repeated = save(client, payload).json()
    assert repeated["generation"]["queue_status"] == "already_queued"
    assert repeated["generation"]["status"] == "generating"
    assert repeated["revision"] == current_preferences["revision"]
    assert store.state["jobs"][TODAY] == active
    assert next_job(client) == {"status": "idle"}
    assert publish(client, edition(leased)).status_code == 200


def test_changed_revision_rejects_both_old_publication_and_failure_without_disturbing_new_lease(setup):
    client, store, _ = setup
    ingest(client, message())
    old = next_job(client)
    payload = update_payload(effective(client))
    payload["sources"][0]["note"] = "新的合成编辑要求。"
    saved = save(client, payload).json()
    new = next_job(client)
    assert new["preferences"]["revision"] == saved["revision"]
    assert new["lease_id"] != old["lease_id"]
    assert old["preferences"]["revision"] == 1
    active = deepcopy(store.state["jobs"][TODAY])
    rejected = publish(client, edition(old))
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "preferences_revision_mismatch"
    rejected = client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=failure(old))
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "preferences_revision_mismatch"
    assert store.state["jobs"][TODAY] == active
    assert TODAY not in store.state["editions"]
    assert publish(client, edition(new)).status_code == 200


def test_management_status_tracks_retry_backoff_and_blocked_generation(setup):
    client, _, current = setup
    ingest(client, message("one"), message("two"))
    assert status(client)["source_count"] == 2
    assert status(client)["status"] == "queued"
    for attempt in range(1, 4):
        job = next_job(client)
        assert job["status"] == "ready"
        assert status(client)["status"] == "generating"
        response = client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=failure(job))
        assert response.status_code == 200
        progress = status(client)
        assert progress["status"] == ("blocked" if attempt == 3 else "retry_wait")
        assert progress["error"] == "codex_failed"
        assert progress["edition_preferences_revision"] is None and progress["generated_at"] is None
        current[0] += timedelta(seconds=60 * 2 ** (attempt - 1) + 1)
    assert next_job(client) == {"status": "idle"}


def test_login_requires_manage_token_and_same_origin_and_issues_a_secure_opaque_cookie(setup):
    client, _, _ = setup
    for role in ("read", "worker", "ingest"):
        assert login(client, TOKENS[role]).status_code == 401
    assert login(client, "incorrect-token-" + "x" * 32).status_code == 401
    assert login(client, origin=None).status_code == 403
    assert login(client, origin="https://evil.example").status_code == 403
    response = login(client)
    assert response.status_code == 200
    cookie_header = response.headers["set-cookie"].lower()
    for attribute in ("httponly", "samesite=strict", "secure", "max-age=28800"):
        assert attribute in cookie_header
    assert len(response.cookies) == 1
    session_value = next(iter(response.cookies.values()))
    assert len(session_value) >= 24 and session_value not in TOKENS.values()
    for token in TOKENS.values():
        assert token not in response.text and token not in response.headers["set-cookie"]
    assert client.get(MANAGE + "/preferences").status_code == 200
    assert client.get(MANAGE + "/status").status_code == 200
    assert_private_headers(response, management=True)


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example", "https://briefing.test.evil.example", "http://briefing.test"])
def test_cookie_writes_require_the_exact_same_origin_and_leave_state_unchanged(setup, origin):
    client, store, _ = setup
    assert login(client).status_code == 200
    payload = update_payload(effective(client, headers={}))
    payload["sources"][0]["note"] = "不应被跨站请求保存。"
    before = deepcopy(store.state)
    headers = {"Origin": origin} if origin is not None else {}
    assert save(client, payload, headers=headers).status_code == 403
    assert client.delete(MANAGE + "/session", headers=headers).status_code == 403
    assert store.state == before
    assert client.get(MANAGE + "/preferences").status_code == 200


def test_cookie_same_origin_save_and_logout_invalidate_the_server_session(setup):
    client, _, _ = setup
    response = login(client)
    assert response.status_code == 200
    cookie_name, cookie_value = next(iter(response.cookies.items()))
    payload = update_payload(effective(client, headers={}))
    payload["sources"][0]["note"] = "同源保存的合成偏好。"
    assert save(client, payload, headers={"Origin": ORIGIN}).status_code == 200
    assert client.delete(MANAGE + "/session", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get(MANAGE + "/preferences").status_code == 401
    replay = {"Cookie": cookie_name + "=" + cookie_value}
    assert client.get(MANAGE + "/preferences", headers=replay).status_code == 401


def test_bearer_without_origin_is_supported_but_foreign_origin_is_rejected(setup):
    client, store, _ = setup
    payload = update_payload(effective(client))
    payload["sources"][0]["note"] = "合成Bearer更新。"
    assert save(client, payload).status_code == 200
    before = deepcopy(store.state)
    foreign = {**auth("manage"), "Origin": "https://evil.example"}
    assert client.get(MANAGE + "/preferences", headers=foreign).status_code == 403
    assert client.get(MANAGE + "/status", headers=foreign).status_code == 403
    assert save(client, update_payload(effective(client)), headers=foreign).status_code == 403
    assert store.state == before
    assert client.delete(MANAGE + "/session", headers=auth("manage")).status_code == 200


def test_cookie_session_expires_after_eight_hours_using_the_store_clock(setup):
    client, _, current = setup
    assert login(client).status_code == 200
    current[0] += timedelta(hours=7, minutes=59)
    assert client.get(MANAGE + "/preferences").status_code == 200
    current[0] += timedelta(minutes=2)
    assert client.get(MANAGE + "/preferences").status_code == 401
    assert client.get(MANAGE + "/preferences", headers=auth("manage")).status_code == 200


def test_only_loopback_http_can_issue_a_session_without_secure_cookie(tmp_path):
    app = create_app(tmp_path, TOKENS, clock=lambda: NOW)
    with TestClient(app, base_url="http://127.0.0.1") as local:
        response = login(local, origin="http://127.0.0.1")
        assert response.status_code == 200
        assert "secure" not in response.headers["set-cookie"].lower()
        assert local.get(MANAGE + "/preferences").status_code == 200
    with TestClient(app, base_url="http://briefing.test") as insecure:
        response = login(insecure, origin="http://briefing.test")
        assert response.status_code in (400, 403)
        assert "set-cookie" not in response.headers


def test_public_static_shell_does_not_embed_settings_or_private_data_and_has_security_headers(setup):
    client, _, _ = setup
    payload = update_payload(effective(client))
    secret_note = "合成私有偏好只应通过鉴权接口读取。"
    payload["sources"][0]["note"] = secret_note
    assert save(client, payload).status_code == 200
    for path, content_type in (("/", "text/html"), ("/app.js", "javascript"), ("/styles.css", "text/css")):
        response = client.get(MANAGE + path)
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]
        assert_private_headers(response, management=True)
        assert secret_note not in response.text
        for source in SOURCE_POLICY["sources"]:
            assert source["name"] not in response.text
        for token in TOKENS.values():
            assert token not in response.text
    redirect = client.get(MANAGE, follow_redirects=False)
    assert redirect.status_code == 307
    assert str(client.base_url.join(redirect.headers["location"])) == ORIGIN + MANAGE + "/"
    assert client.get(MANAGE + "/preferences").status_code == 401
