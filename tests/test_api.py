from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from briefing.api import PREFIX, create_app
from briefing.models import MAX_SECTIONS, MAX_SECTION_CHARS, Edition
from briefing.source_policy import SOURCE_POLICY

TOKENS = {role: role + "-" + "x" * 32 for role in ("read", "worker", "ingest")}


def auth(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


def message(id="mail1", day="2026-10-26", body="测试正文，项目尚未完成。"):
    return {"id": id, "subject": f"[每日早报][每日资讯][{day}]", "received_at": "2026-10-27T00:30:00Z", "body": "早报事件类型：定时结果\n" + body,
            "source_url": "https://example.com/report"}


@pytest.fixture
def setup(tmp_path):
    current = [datetime(2026, 10, 27, 1, tzinfo=timezone.utc)]
    app = create_app(tmp_path, TOKENS, clock=lambda: current[0])
    with TestClient(app) as client:
        yield client, app.state.store, current


def ingest(client, messages):
    return client.post(PREFIX + "/ingest", headers=auth("ingest"), json={"messages": messages})


def job(client):
    return client.get(PREFIX + "/jobs/next", headers=auth("worker")).json()


def edition(source, texts=None):
    texts = texts or [("项目", "项目进展尚未验证。")]
    ids = sorted(m["id"] for m in source["messages"])
    return {"date": source["date"], "title": "测试早报", "generated_at": "2026-10-27T00:45:00Z",
            "source_message_ids": ids, "source_dates": [source["date"]], "fingerprint": source["fingerprint"],
            "rebuild_id": source.get("rebuild_id"), "lease_id": source.get("lease_id"),
            "preferences_revision": source["preferences"]["revision"], "format": "mixed",
            "sections": [{"title": f"第{i}条", "topic": topic, "text": text, "source_ids": ids} for i, (topic, text) in enumerate(texts, 1)],
            "warnings": []}


def publish(client, payload):
    return client.post(PREFIX + "/editions", headers=auth("worker"), json=payload)


@pytest.mark.parametrize("path,method,allowed", [("read", "get", "read"), ("jobs/next", "get", "worker"), ("ingest", "post", "ingest"), ("editions", "post", "worker"), ("jobs/failure", "post", "worker"), ("jobs/rebuild", "post", "worker")])
def test_scoped_auth(setup, path, method, allowed):
    client, _, _ = setup
    for role in (None, *TOKENS):
        if role == allowed:
            continue
        response = getattr(client, method)(PREFIX + "/" + path, headers=auth(role) if role else {})
        assert response.status_code == 401
        assert "body" not in response.text
    assert client.get(PREFIX + "/health").json() == {"status": "ok", "service": "daily-briefing"}


def test_tokens_fail_closed(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(tmp_path, {"read": "x", "ingest": "x", "worker": "x"})


def test_ingest_dedup_conflicts_preserve_original_and_subject_day(setup):
    client, store, _ = setup
    original = message(body="  原文不能trim。\n")
    assert ingest(client, [original]).json()["accepted"] == 1
    assert ingest(client, [original]).json() == {"status": "accepted", "accepted": 0, "duplicates": 1}
    changed = {**original, "body": "早报事件类型：定时结果\n不应覆盖"}
    assert ingest(client, [message(id="new"), changed]).status_code == 409
    source = job(client)
    assert source["date"] == "2026-10-26"
    assert len(source["messages"]) == 1
    assert source["messages"][0]["body"] == original["body"]
    assert len(store.state["messages"]) == 1


@pytest.mark.parametrize("subject", ["随便一封私人邮件", "[每日早报][每日资讯][2026-02-30]", "[每日早报][每日资讯][2026-10-26] trailing"])
def test_unrelated_and_invalid_subjects_excluded_without_body_echo(setup, subject):
    client, _, _ = setup
    secret_body = "此处是私密内容，不可回显"
    response = ingest(client, [{**message(body=secret_body), "subject": subject}])
    assert response.status_code == 422
    assert secret_body not in response.text
    assert job(client) == {"status": "idle"}


def test_unified_subject_and_timezone_required(setup):
    client, _, _ = setup
    assert ingest(client, [message()]).status_code == 200
    assert ingest(client, [{**message(id="bad"), "received_at": "2026-10-27T12:00:00"}]).status_code == 422
    assert ingest(client, [message(id="future", day="2026-10-28")]).status_code == 409


@pytest.mark.parametrize("subject", [
    "Internal Sample 项目日报｜2026-10-26",
    "Internal Sample 项目日报|2026-10-26",
    "[每日早报][Internal Sample 项目日报][2026-10-26]",
    "[每日早报][ Internal Sample 项目日报 ][2026-10-26]",
    "[每日早报][PERSONAL  EVOLUTOR\t项目日报][2026-10-26]",
])
def test_unregistered_report_is_rejected_even_with_unified_title(setup, subject):
    client, store, _ = setup
    excluded = {**message(), "subject": subject, "body": "仅用于拒收测试的合成正文。"}
    response = ingest(client, [excluded])
    assert response.status_code == 422
    assert excluded["body"] not in response.text
    assert store.state["messages"] == {}
    assert job(client) == {"status": "idle"}
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["status"] == "missing"


@pytest.mark.parametrize("source", SOURCE_POLICY["sources"], ids=lambda source: source["name"])
def test_only_selected_source_with_its_exact_event_marker_enters_api(setup, source):
    client, store, _ = setup
    selected = {**message(), "subject": f"[每日早报][{source['name']}][2026-10-26]",
                "body": "早报事件类型：" + source["event_type"] + "\r\n合成的已整理正文。"}
    assert ingest(client, [selected]).json()["accepted"] == 1
    assert store.state["messages"][selected["id"]]["body"] == selected["body"]


@pytest.mark.parametrize("body", [
    "没有首行标记的合成正文。", "早报事件类型:定时结果\n合成正文。", "\n早报事件类型：定时结果\n合成正文。",
    "早报事件类型：新回复\n合成正文。", "早报事件类型：定时结果", "早报事件类型：定时结果\r\n \r\n\t",
    "早报事件类型：定时结果 \n合成正文。",
])
def test_daily_event_marker_is_required_exact_and_followed_by_content(setup, body):
    client, store, _ = setup
    assert ingest(client, [{**message(), "body": body}]).status_code == 422
    assert store.state["messages"] == {}


@pytest.mark.parametrize("source", [source for source in SOURCE_POLICY["sources"] if source["kind"] == "conditional"], ids=lambda source: source["name"])
def test_conditional_source_does_not_accept_unmarked_or_wrong_event_type(setup, source):
    client, store, _ = setup
    for body in ["合成的新回复内容，但没有机器标记。", "早报事件类型：定时结果\n合成正文。",
                 "早报事件类型：" + ("新信息" if source["event_type"] == "新回复" else "新回复") + "\n合成正文。"]:
        selected = {**message(), "subject": f"[每日早报][{source['name']}][2026-10-26]", "body": body}
        assert ingest(client, [selected]).status_code == 422
    assert store.state["messages"] == {}


@pytest.mark.parametrize("source", [source for source in SOURCE_POLICY["sources"] if source["kind"] == "weekly"], ids=lambda source: source["name"])
def test_weekly_source_requires_a_completed_scheduled_result(setup, source):
    client, store, _ = setup
    for body in ["没有首行标记的合成周报。", "早报事件类型：新回复\n合成正文。",
                 "早报事件类型：新信息\n合成正文。", "早报事件类型：定时结果\n \t"]:
        selected = {**message(), "subject": f"[每日早报][{source['name']}][2026-10-23]", "body": body}
        assert ingest(client, [selected]).status_code == 422
    assert store.state["messages"] == {}


@pytest.mark.parametrize("source", [source for source in SOURCE_POLICY["sources"] if source["kind"] == "weekly"], ids=lambda source: source["name"])
def test_weekly_source_keeps_its_subject_date_without_rescheduling_generation_or_reads(setup, source):
    client, _, _ = setup
    selected = {**message(), "subject": f"[每日早报][{source['name']}][2026-10-23]"}
    assert ingest(client, [selected]).json()["accepted"] == 1
    pending = job(client)
    assert pending["date"] == "2026-10-23"
    assert publish(client, edition(pending)).json()["status"] == "published"
    latest = client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()
    assert latest["date"] == "2026-10-23"
    today = client.get(PREFIX + "/read?date=today", headers=auth("read")).json()
    assert today["status"] == "missing"
    assert today["full_spoken_text"] == ""


@pytest.mark.parametrize("task", ["未选择的任务", "自复制 AI 开源复现追踪", "每日资讯 副本"])
def test_unselected_tasks_are_not_accepted_even_with_valid_daily_marker(setup, task):
    client, store, _ = setup
    assert ingest(client, [{**message(), "subject": f"[每日早报][{task}][2026-10-26]"}]).status_code == 422
    assert store.state["messages"] == {}


def test_read_today_latest_pagination_and_topic(setup):
    client, _, _ = setup
    ingest(client, [message()])
    payload = edition(job(client), [("AI", "第一条事实。"), ("贸易", "第二条尚待确认。"), ("AI", "第三条事实。")])
    assert publish(client, payload).status_code == 200
    missing = client.get(PREFIX + "/read", headers=auth("read")).json()
    assert missing["status"] == "missing" and missing["date"] == "2026-10-27"
    assert missing["latest_date"] == "2026-10-26"
    first = client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()
    assert first["status"] == "ready" and first["next_section"] == 2
    assert "2026-10-26" in first["spoken_text"]
    assert "body" not in first["sources"][0]
    last = client.post(PREFIX + "/read", headers=auth("read"), json={"date": "2026-10-26", "section": 2, "topic": "AI"}).json()
    assert last["total_sections"] == 2 and last["next_section"] is None
    assert "第三条" in last["spoken_text"]
    assert client.get(PREFIX + "/read?date=latest&section=4", headers=auth("read")).json()["status"] == "finished"
    assert client.get(PREFIX + "/read?date=latest&topic=科技", headers=auth("read")).json()["status"] == "topic_missing"
    assert client.get(PREFIX + "/read?date=bad", headers=auth("read")).status_code == 422


def test_full_read_preserves_order_actual_date_sources_and_existing_page(setup):
    client, store, _ = setup
    ingest(client, [message(id="mail1"), message(id="mail2"), message(id="mail3")])
    payload = edition(job(client), [("AI", "第一项事实。"), ("贸易", "第二项尚待确认。"), ("AI", "第三项事实。")])
    for index, section in enumerate(payload["sections"], 1):
        section["source_ids"] = [f"mail{index}"]
    payload["warnings"] = ["仅用于审计的覆盖范围备注，不应朗读。"]
    assert publish(client, payload).status_code == 200
    before = deepcopy(store.state)
    result = client.get(PREFIX + "/read?date=latest&section=2&view=full", headers=auth("read")).json()
    assert result["status"] == "ready" and result["section"] == 2 and result["next_section"] == 3
    assert result["spoken_text"] == "这期早报日期是2026-10-26。第2段，共3段。第二项尚待确认。"
    assert [source["id"] for source in result["sources"]] == ["mail2"]
    assert result["full_spoken_text"] == (
        "这期早报日期是2026-10-26。\n\n第1段，共3段。第一项事实。\n\n"
        "第2段，共3段。第二项尚待确认。\n\n第3段，共3段。第三项事实。\n\n本次早报内容已播完。")
    assert [source["id"] for source in result["full_sources"]] == ["mail1", "mail2", "mail3"]
    assert all("body" not in source for source in result["full_sources"])
    assert payload["warnings"][0] not in result["full_spoken_text"]
    assert "https://" not in result["full_spoken_text"]
    assert result["warnings"] == payload["warnings"]
    assert store.state == before


def test_full_read_filters_topic_with_own_numbering_and_deduplicates_sources(setup):
    client, _, _ = setup
    ingest(client, [message(id="ai"), message(id="trade")])
    payload = edition(job(client), [("AI", "AI第一段。"), ("贸易", "贸易段不应混入。"), ("AI", "AI第二段。")])
    for section in payload["sections"]:
        section["source_ids"] = ["ai" if section["topic"] == "AI" else "trade"]
    assert publish(client, payload).status_code == 200
    result = client.post(PREFIX + "/read", headers=auth("read"), json={"date": "latest", "topic": "AI", "section": 2, "view": "full"}).json()
    assert result["total_sections"] == 2 and result["next_section"] is None
    assert result["spoken_text"].endswith("AI第二段。")
    assert result["full_spoken_text"] == (
        "这期早报日期是2026-10-26。\n\n第1段，共2段。AI第一段。\n\n第2段，共2段。AI第二段。\n\n本次早报内容已播完。")
    assert [source["id"] for source in result["full_sources"]] == ["ai"]
    finished = client.get(PREFIX + "/read?date=latest&topic=AI&section=3&view=full", headers=auth("read")).json()
    assert finished["status"] == "finished" and finished["sources"] == []
    assert finished["spoken_text"] == "2026-10-26的早报已经播完。"
    assert finished["full_spoken_text"] == result["full_spoken_text"]


def test_missing_date_or_topic_never_fabricates_full_read(setup):
    client, _, _ = setup
    empty = client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()
    assert empty["status"] == "missing" and empty["full_spoken_text"] == "" and empty["full_sources"] == []
    ingest(client, [message()])
    assert publish(client, edition(job(client))).status_code == 200
    for query, status in [("date=today", "missing"), ("date=latest&topic=不存在", "topic_missing")]:
        result = client.get(PREFIX + "/read?" + query, headers=auth("read")).json()
        assert result["status"] == status
        assert result["full_spoken_text"] == "" and result["full_sources"] == []


def test_full_read_preserves_all_thirty_two_by_six_hundred_characters(setup):
    client, _, _ = setup
    assert MAX_SECTIONS == 32 and MAX_SECTION_CHARS == 600
    ingest(client, [message()])
    payload = edition(job(client), [("AI", "字" * MAX_SECTION_CHARS) for _ in range(MAX_SECTIONS)])
    assert publish(client, payload).status_code == 200
    result = client.get(PREFIX + "/read?date=latest&view=full", headers=auth("read")).json()
    assert result["total_sections"] == MAX_SECTIONS
    assert result["full_spoken_text"].count("字") == MAX_SECTIONS * MAX_SECTION_CHARS
    assert f"第{MAX_SECTIONS}段，共{MAX_SECTIONS}段。" in result["full_spoken_text"]
    assert result["full_spoken_text"].endswith("本次早报内容已播完。")
    assert len(result["full_sources"]) == 1
    last = client.get(PREFIX + f"/read?date=latest&section={MAX_SECTIONS}&view=full", headers=auth("read")).json()
    assert last["spoken_text"].endswith("字" * MAX_SECTION_CHARS) and last["next_section"] is None
    assert last["full_spoken_text"] == result["full_spoken_text"]
    finished = client.get(PREFIX + f"/read?date=latest&section={MAX_SECTIONS + 1}&view=full", headers=auth("read")).json()
    assert finished["status"] == "finished"


@pytest.mark.parametrize("mutate", [
    lambda p: p["sections"][0].update(text="字" * (MAX_SECTION_CHARS + 1)),
    lambda p: p.update(sections=p["sections"] * (MAX_SECTIONS + 1)),
    lambda p: p["sections"][0].update(source_ids=["not-a-source"]),
    lambda p: p.update(source_dates=["2026-10-25"]),
    lambda p: p.update(source_message_ids=[]),
    lambda p: p.update(generated_at="2026-10-28T00:00:00Z"),
])
def test_invalid_edition_never_replaces_good(setup, mutate):
    client, _, _ = setup
    ingest(client, [message()])
    good = edition(job(client))
    assert publish(client, good).status_code == 200
    bad = deepcopy(good)
    mutate(bad)
    assert publish(client, bad).status_code in (409, 422)
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith("项目进展尚未验证。")


def test_late_mail_changes_job_and_rejects_old_output(setup):
    client, _, _ = setup
    ingest(client, [message()])
    old = job(client)
    ingest(client, [message(id="mail2")])
    new = job(client)
    assert new["fingerprint"] != old["fingerprint"]
    assert publish(client, edition(old)).status_code == 409
    assert publish(client, edition(new)).json()["status"] == "published"
    assert publish(client, edition(new)).json()["status"] == "duplicate"
    assert job(client)["status"] == "idle"


def test_failure_backoff_persists_blocks_after_three_and_new_mail_resets(setup):
    client, store, current = setup
    ingest(client, [message()])
    for attempt in range(3):
        source = job(client)
        assert source["status"] == "ready"
        response = client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json={"date": source["date"], "fingerprint": source["fingerprint"], "code": "timeout", "lease_id": source["lease_id"], "preferences_revision": source["preferences"]["revision"]})
        assert response.json()["attempts"] == attempt + 1
        assert job(client)["status"] == "idle"
        current[0] += timedelta(hours=1)
    assert response.json()["status"] == "blocked"
    restarted = create_app(store.directory, TOKENS, clock=lambda: current[0])
    with TestClient(restarted) as second:
        assert job(second)["status"] == "idle"
        ingest(second, [message(id="late")])
        assert job(second)["status"] == "ready"


def test_crashed_workers_have_bounded_leases(setup):
    client, _, current = setup
    ingest(client, [message()])
    for _ in range(3):
        assert job(client)["status"] == "ready"
        assert job(client)["status"] == "idle"
        current[0] += timedelta(minutes=16)
    assert job(client)["status"] == "idle"


def test_today_uses_shanghai_not_utc(setup):
    client, _, current = setup
    current[0] = datetime(2026, 10, 26, 17, tzinfo=timezone.utc)
    assert client.get(PREFIX + "/read", headers=auth("read")).json()["date"] == "2026-10-27"


def rebuild(client, day="2026-10-26"):
    return client.post(PREFIX + "/jobs/rebuild", headers=auth("worker"), json={"date": day})


def test_manual_same_source_rebuild_retains_good_edition_until_publish_and_archives(setup):
    client, store, _ = setup
    ingest(client, [message()])
    original = edition(job(client))
    assert publish(client, original).status_code == 200
    assert job(client)["status"] == "idle"
    changed = deepcopy(original)
    changed["sections"][0]["text"] = "先关注尚待解决的具体项目问题。"
    # A changed body alone cannot overwrite a successfully published fingerprint.
    assert publish(client, changed).json()["status"] == "duplicate"
    request = rebuild(client).json()
    assert request["status"] == "queued"
    assert rebuild(client).json()["rebuild_id"] == request["rebuild_id"]
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith(original["sections"][0]["text"])
    source = job(client)
    assert source["rebuild_id"] == request["rebuild_id"]
    attempts = store.state["jobs"]["2026-10-26"]["attempts"]
    assert rebuild(client).json()["status"] == "already_queued"
    assert store.state["jobs"]["2026-10-26"]["attempts"] == attempts
    assert publish(client, changed).status_code == 409
    changed["rebuild_id"] = source["rebuild_id"]
    changed["lease_id"] = source["lease_id"]
    assert publish(client, changed).json()["status"] == "published"
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith(changed["sections"][0]["text"])
    assert store.state["history"]["2026-10-26"][0]["sections"][0]["text"] == original["sections"][0]["text"]
    assert publish(client, changed).json()["status"] == "duplicate"
    assert len(store.state["history"]["2026-10-26"]) == 1
    assert job(client)["status"] == "idle"


def test_rebuild_failure_preserves_good_edition_and_stops_after_three_attempts(setup):
    client, store, current = setup
    ingest(client, [message()])
    good = edition(job(client))
    publish(client, good)
    queued = rebuild(client).json()
    for attempt in range(3):
        source = job(client)
        assert source["rebuild_id"] == queued["rebuild_id"]
        # Even the third active attempt must survive a repeated rebuild request.
        assert rebuild(client).json()["status"] == "already_queued"
        assert store.state["jobs"]["2026-10-26"]["attempts"] == attempt + 1
        result = client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json={"date": source["date"], "fingerprint": source["fingerprint"], "code": "timeout", "rebuild_id": source["rebuild_id"], "lease_id": source["lease_id"], "preferences_revision": source["preferences"]["revision"]})
        assert result.json()["attempts"] == attempt + 1
        assert job(client)["status"] == "idle"
        assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith(good["sections"][0]["text"])
        current[0] += timedelta(hours=1)
    assert result.json()["status"] == "blocked"
    assert store.state.get("history", {}) == {}
    restarted = create_app(store.directory, TOKENS, clock=lambda: current[0])
    with TestClient(restarted) as second:
        assert job(second)["status"] == "idle"
        assert second.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["status"] == "ready"
        reset = rebuild(second).json()
        assert reset["rebuild_id"] != queued["rebuild_id"]
        assert job(second)["rebuild_id"] == reset["rebuild_id"]


def test_stale_rebuild_ids_and_unleased_publication_cannot_replace_or_fail_current_job(setup):
    client, store, _ = setup
    ingest(client, [message()])
    good = edition(job(client))
    publish(client, good)
    queued = rebuild(client).json()
    no_lease = {**good, "rebuild_id": queued["rebuild_id"]}
    assert publish(client, no_lease).json()["detail"] == "rebuild_lease_not_active"
    source = job(client)
    stale = {**good, "rebuild_id": "0" * 32}
    assert publish(client, stale).json()["detail"] == "rebuild_job_mismatch"
    result = client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json={"date": source["date"], "fingerprint": source["fingerprint"], "code": "invalid_output", "preferences_revision": source["preferences"]["revision"]})
    assert result.status_code == 409
    assert store.state["jobs"]["2026-10-26"]["lease_until"] > 0
    bad = {**good, "rebuild_id": source["rebuild_id"], "sections": []}
    assert publish(client, bad).status_code == 422
    assert store.state["editions"]["2026-10-26"]["sections"] == good["sections"]


def test_rebuild_requires_existing_sources_and_private_history_is_not_exposed(setup):
    client, store, _ = setup
    assert rebuild(client).json()["detail"] == "source_date_not_found"
    assert store.state["jobs"] == {}
    assert client.get(PREFIX + "/history", headers=auth("read")).status_code == 404


def test_previous_rebuild_attempt_cannot_publish_or_cancel_a_new_lease(setup):
    client, store, current = setup
    ingest(client, [message()])
    good = edition(job(client))
    publish(client, good)
    rebuild(client)
    old = job(client)
    current[0] += timedelta(seconds=901)
    fresh = job(client)
    assert old["rebuild_id"] == fresh["rebuild_id"]
    assert old["lease_id"] != fresh["lease_id"]
    stale_output = {**good, "rebuild_id": old["rebuild_id"], "lease_id": old["lease_id"]}
    assert publish(client, stale_output).json()["detail"] == "rebuild_lease_mismatch"
    stale_failure = {"date": old["date"], "fingerprint": old["fingerprint"], "rebuild_id": old["rebuild_id"], "lease_id": old["lease_id"], "code": "timeout", "preferences_revision": old["preferences"]["revision"]}
    assert client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=stale_failure).json()["detail"] == "rebuild_lease_mismatch"
    assert store.state["jobs"]["2026-10-26"]["lease_id"] == fresh["lease_id"]
    assert store.state["jobs"]["2026-10-26"]["lease_until"] > current[0].timestamp()
    assert job(client)["status"] == "idle"
    fresh_output = {**good, "rebuild_id": fresh["rebuild_id"], "lease_id": fresh["lease_id"]}
    assert publish(client, fresh_output).json()["status"] == "published"


def test_previous_initial_attempt_cannot_publish_or_cancel_a_new_lease(setup):
    client, store, current = setup
    ingest(client, [message()])
    old = job(client)
    current[0] += timedelta(seconds=901)
    fresh = job(client)
    assert old["lease_id"] and fresh["lease_id"] != old["lease_id"]
    before = deepcopy(store.state)
    assert publish(client, edition(old)).json()["detail"] == "job_lease_mismatch"
    stale_failure = {"date": old["date"], "fingerprint": old["fingerprint"], "code": "timeout", "lease_id": old["lease_id"], "preferences_revision": old["preferences"]["revision"]}
    assert client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=stale_failure).json()["detail"] == "job_lease_mismatch"
    assert store.state == before
    current[0] += timedelta(seconds=121)
    assert job(client)["status"] == "idle"
    assert publish(client, edition(fresh)).json()["status"] == "published"
    published = deepcopy(store.state)
    assert publish(client, edition(old)).json()["detail"] == "job_lease_mismatch"
    assert store.state == published


def test_initial_publication_and_failure_require_the_active_lease(setup):
    client, store, current = setup
    ingest(client, [message()])
    source = job(client)
    before = deepcopy(store.state)
    missing_lease = {**edition(source), "lease_id": None}
    assert publish(client, missing_lease).json()["detail"] == "job_lease_mismatch"
    failure = {"date": source["date"], "fingerprint": source["fingerprint"], "code": "timeout", "preferences_revision": source["preferences"]["revision"]}
    assert client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=failure).json()["detail"] == "job_lease_mismatch"
    current[0] += timedelta(seconds=901)
    assert publish(client, edition(source)).json()["detail"] == "job_lease_not_active"
    failure["lease_id"] = source["lease_id"]
    assert client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=failure).json()["detail"] == "job_lease_not_active"
    assert store.state == before


@pytest.mark.parametrize("manual", [False, True])
def test_successful_publication_is_idempotent_and_late_failure_cannot_recreate_job(setup, manual):
    client, store, _ = setup
    ingest(client, [message()])
    source = job(client)
    if manual:
        assert publish(client, edition(source)).json()["status"] == "published"
        rebuild(client)
        source = job(client)
    output = edition(source)
    assert publish(client, output).json()["status"] == "published"
    before = deepcopy(store.state)
    assert publish(client, output).json()["status"] == "duplicate"
    failure = {"date": source["date"], "fingerprint": source["fingerprint"], "code": "publish_failed",
               "rebuild_id": source["rebuild_id"], "lease_id": source["lease_id"], "preferences_revision": source["preferences"]["revision"]}
    assert client.post(PREFIX + "/jobs/failure", headers=auth("worker"), json=failure).status_code == 409
    assert store.state == before
    assert job(client)["status"] == "idle"


def selected_message(id, index):
    source = SOURCE_POLICY["sources"][index]
    return {**message(id=id), "subject": f"[每日早报][{source['name']}][2026-10-26]",
            "body": "早报事件类型：" + source["event_type"] + "\n合成来源正文。"}


def section(ids, text, topic="不可信的输出主题"):
    return {"title": "合成段落", "topic": topic, "text": text, "source_ids": ids}


def test_publish_sorts_all_selected_sources_using_mail_subject_not_output_topic(setup):
    client, _, _ = setup
    source_count = len(SOURCE_POLICY["sources"])
    assert source_count == 10
    assert ingest(client, [selected_message(f"source-{index}", index) for index in range(source_count)]).status_code == 200
    payload = edition(job(client))
    payload["sections"] = [section([f"source-{index}"], f"来源{index}的正文。", SOURCE_POLICY["sources"][source_count - 1 - index]["name"])
                           for index in reversed(range(source_count))]
    assert publish(client, payload).json()["status"] == "published"
    for index in range(source_count):
        result = client.get(PREFIX + f"/read?date=latest&section={index + 1}&view=full", headers=auth("read")).json()
        assert result["spoken_text"].endswith(f"来源{index}的正文。")
        assert [source["id"] for source in result["sources"]] == [f"source-{index}"]


def test_publish_keeps_same_source_segment_order_and_allows_merged_mail(setup):
    client, store, _ = setup
    assert ingest(client, [selected_message("daily", 0), selected_message("gold-a", 1),
                           selected_message("gold-b", 1), selected_message("training", 2)]).status_code == 200
    payload = edition(job(client))
    payload["sections"] = [section(["gold-b", "gold-a"], "黄金第一段，合并两封邮件。"),
                           section(["training"], "训练段。"), section(["gold-a"], "黄金第二段。"),
                           section(["daily"], "个性化简报段。")]
    assert publish(client, payload).json()["status"] == "published"
    expected = ["个性化简报段。", "黄金第一段，合并两封邮件。", "黄金第二段。", "训练段。"]
    for index, text in enumerate(expected, 1):
        result = client.get(PREFIX + f"/read?date=latest&section={index}&view=full", headers=auth("read")).json()
        assert result["spoken_text"].endswith(text)
    assert store.state["editions"]["2026-10-26"]["sections"][1]["source_ids"] == ["gold-b", "gold-a"]


def test_mixed_source_publication_cannot_replace_good_edition_or_change_state(setup):
    client, store, _ = setup
    ingest(client, [selected_message("daily", 0), selected_message("gold", 1)])
    good = edition(job(client))
    good["sections"] = [section(["daily"], "原有个性化简报。"), section(["gold"], "原有黄金段。")]
    assert publish(client, good).status_code == 200
    rebuild(client)
    mixed = edition(job(client))
    mixed["sections"] = [section(["daily", "gold"], "混合两个任务的段落。", "每日资讯")]
    before, on_disk = deepcopy(store.state), store.path.read_bytes()
    response = publish(client, mixed)
    assert response.status_code == 409
    assert response.json()["detail"] == "section_mixed_source_tasks"
    assert store.state == before and store.path.read_bytes() == on_disk
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith("原有个性化简报。")


def test_unknown_stored_source_cannot_publish_under_an_allowed_output_topic(setup):
    client, store, _ = setup
    ingest(client, [message()])
    # A source retained from before a stricter policy must also fail at publication.
    legacy = deepcopy(store.state)
    legacy["messages"]["mail1"]["subject"] = "[每日早报][未选来源][2026-10-26]"
    store._commit(legacy)
    payload = edition(job(client))
    payload["sections"][0]["topic"] = "每日资讯"
    before, on_disk = deepcopy(store.state), store.path.read_bytes()
    response = publish(client, payload)
    assert response.status_code == 409
    assert response.json()["detail"] == "source_task_not_allowed"
    assert store.state == before and store.path.read_bytes() == on_disk


def test_store_publication_sorts_a_copy_without_mutating_the_callers_edition(setup):
    client, store, _ = setup
    ingest(client, [selected_message("daily", 0), selected_message("gold", 1)])
    payload = edition(job(client))
    payload["sections"] = [section(["gold"], "黄金段。"), section(["daily"], "个性化简报段。")]
    caller_edition = Edition.model_validate(payload)
    before = caller_edition.model_dump()
    assert store.publish(caller_edition)["status"] == "published"
    assert caller_edition.model_dump() == before
    assert client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()["spoken_text"].endswith("个性化简报段。")


@pytest.mark.parametrize("invalid", ["unknown", "duplicate_id", "mixed", "partial", "duplicate_summary", "news"])
def test_invalid_learning_summary_cannot_replace_good_edition(setup, invalid):
    client, store, _ = setup
    ingest(client, [selected_message("daily", 0), selected_message("lesson-a", 2), selected_message("lesson-b", 2)])
    good = edition(job(client))
    good["sections"] = [section(["daily"], "新闻正文。"), section(["lesson-a", "lesson-b"], "完整学习内容。")]
    good["learning_summaries"] = [section(["lesson-a", "lesson-b"], "今天学习的主题与意义。", "学习提示")]
    assert publish(client, good).status_code == 200
    rebuild(client)
    bad = {**deepcopy(good), **{key: value for key, value in edition(job(client)).items() if key in ("lease_id", "rebuild_id")}}
    summary = bad["learning_summaries"][0]
    if invalid == "unknown":
        summary["source_ids"] = ["unknown"]
    elif invalid == "duplicate_id":
        summary["source_ids"] = ["lesson-a", "lesson-b", "lesson-a"]
    elif invalid == "mixed":
        summary["source_ids"] = ["lesson-a", "daily"]
    elif invalid == "partial":
        summary["source_ids"] = ["lesson-a"]
    elif invalid == "duplicate_summary":
        bad["learning_summaries"].append(deepcopy(summary))
    else:
        summary["source_ids"] = ["daily"]
    before, on_disk = deepcopy(store.state), store.path.read_bytes()
    rejected = publish(client, bad)
    assert rejected.status_code == 409 and rejected.json()["detail"].startswith("learning_summary_")
    assert store.state == before and store.path.read_bytes() == on_disk
    daily = client.get(PREFIX + "/read?date=latest", headers=auth("read")).json()
    assert "今天学习的主题与意义。" in daily["full_spoken_text"]
    assert "完整学习内容。" not in daily["full_spoken_text"]
