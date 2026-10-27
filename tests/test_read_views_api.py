"""HTTP isolation and pagination checks for cached briefing views."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import pytest

from briefing.api import PREFIX, create_app
from briefing.preferences import UpdatePreferences
from briefing.source_policy import SOURCE_RULES

TODAY = "2026-10-27"
NOW = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)
TOKENS = {role: role + "-" + "x" * 32 for role in ("read", "worker", "ingest")}
NEWS = "市场观察"
LESSON = "每日知识练习"
RUSSIAN = "外语练习"
WORK = "项目邮件回复"
NEWS_TEXT = "合成新闻NEWS-CONTENT：报价仍待确认。"
LESSON_A = "合成课程LESSON-A：" + "先区分观察事实和因果解释，例子中的条件必须保留。" * 12
LESSON_B = "合成练习LESSON-B：比较两个完整情景，分别写出条件和判断理由；原稿未提供答案。"
RUSSIAN_TEXT = "合成俄语RUSSIAN-FULL：Как вы это понимаете? 请保留原句并说明你对这件事的理解。"
WORK_TEXT = "合成工作WORK-CONTENT：私有联络事项只在工作日程开启后可读。"
LESSON_SUMMARY = "合成学习概览SUMMARY-LESSON：今天练习区分观察事实与因果解释。"
RUSSIAN_SUMMARY = "合成学习概览SUMMARY-RUSSIAN：今天练习一个俄语问句。"
PRIVATE_TITLE = "合成整期标题WORK-TITLE包含工作提示"
PRIVATE_WARNING = "合成后台备注WORK-WARNING包含工作限定"


def auth(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path, TOKENS, clock=lambda: NOW)
    with TestClient(app, base_url="https://briefing.test") as client:
        yield client, app.state.store


def set_preferences(store, *, categories=None, work_enabled=None):
    current = store.preferences()
    payload = {"expected_revision": current["revision"],
               "work_enabled": current["work_enabled"] if work_enabled is None else work_enabled,
               "sources": [{key: source[key] for key in ("name", "category", "mode", "narration_detail", "note")}
                           for source in current["sources"]]}
    for source in payload["sources"]:
        if source["name"] in (categories or {}):
            source["category"] = categories[source["name"]]
    return store.update_preferences(UpdatePreferences.model_validate(payload))


def section(text, *ids, topic="合成主题"):
    return {"title": "合成段落标题", "topic": topic, "text": text, "source_ids": list(ids)}


def publish_cache(client, sources, sections, summaries=()):
    messages = [{"id": identifier, "subject": f"[每日早报][{source}][{TODAY}]",
                 "received_at": NOW.isoformat(), "body": "早报事件类型：" + SOURCE_RULES[source]["event_type"] + "\n合成原始资料。",
                 "source_url": f"https://example.com/synthetic/{identifier}"}
                for identifier, source in sources]
    response = client.post(PREFIX + "/ingest", headers=auth("ingest"), json={"messages": messages})
    assert response.status_code == 200, response.text
    job = client.get(PREFIX + "/jobs/next", headers=auth("worker")).json()
    assert job["status"] == "ready" and job["date"] == TODAY
    payload = {"date": TODAY, "generated_at": NOW.isoformat(), "title": PRIVATE_TITLE,
               "sections": sections, "learning_summaries": list(summaries), "warnings": [PRIVATE_WARNING],
               "source_message_ids": sorted(message["id"] for message in messages), "source_dates": [TODAY],
               "fingerprint": job["fingerprint"], "rebuild_id": job.get("rebuild_id"), "lease_id": job["lease_id"],
               "preferences_revision": job["preferences"]["revision"], "format": "mixed"}
    response = client.post(PREFIX + "/editions", headers=auth("worker"), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"


@pytest.fixture
def cached(setup):
    client, store = setup
    set_preferences(store, categories={WORK: "work"})
    publish_cache(client,
                  [("work", WORK), ("lesson-b", LESSON), ("news", NEWS), ("lesson-a", LESSON), ("russian", RUSSIAN)],
                  [section(WORK_TEXT, "work", topic="新闻"), section(LESSON_A, "lesson-a", topic="定义"),
                   section(NEWS_TEXT, "news", topic="新闻"), section(LESSON_B, "lesson-b", topic="练习"),
                   section(RUSSIAN_TEXT, "russian", topic="俄语")],
                  [section(RUSSIAN_SUMMARY, "russian", topic="俄语概览"),
                   section(LESSON_SUMMARY, "lesson-b", "lesson-a", topic="学科概览")])
    return client, store


def read(client, method="GET", **parameters):
    if method == "GET":
        response = client.get(PREFIX + "/read", headers=auth("read"), params=parameters)
    else:
        response = client.post(PREFIX + "/read", headers=auth("read"), json=parameters)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def source_ids(result, key="full_sources"):
    return {source["id"] for source in result[key]}


def assert_empty_content(result, *, empty_title=True, no_cache_metadata=True):
    assert result["full_spoken_text"] == ""
    assert result["sources"] == result["full_sources"] == result["source_dates"] == []
    assert result["total_sections"] == 0 and result["next_section"] is None
    if empty_title:
        assert result["title"] == ""
    if no_cache_metadata:
        assert "generated_at" not in result and "warnings" not in result


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_default_daily_uses_cached_learning_overviews_and_full_news_only(cached, method):
    client, store = cached
    before = deepcopy(store.state)
    first = read(client, method)
    assert first["view"] == "daily" and first["status"] == "ready"
    assert first["total_sections"] == 3 and first["next_section"] == 2
    assert first["spoken_text"].endswith(NEWS_TEXT)
    for index, text in enumerate((NEWS_TEXT, LESSON_SUMMARY, RUSSIAN_SUMMARY), 1):
        assert f"第{index}段，共3段。" + text in first["full_spoken_text"]
    for hidden in (LESSON_A, LESSON_B, RUSSIAN_TEXT, WORK_TEXT, PRIVATE_TITLE, PRIVATE_WARNING):
        assert hidden not in json.dumps(first, ensure_ascii=False)
    assert source_ids(first) == {"news", "lesson-a", "lesson-b", "russian"}
    assert source_ids(first, "sources") == {"news"}
    assert first["source_dates"] == [TODAY] and first["warnings"] == []
    overview = read(client, method, view="daily", section=2)
    assert overview["spoken_text"].endswith(LESSON_SUMMARY)
    assert source_ids(overview, "sources") == {"lesson-a", "lesson-b"}
    assert store.state == before


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_learning_returns_complete_text_with_view_local_page_numbers_and_sources(cached, method):
    client, _ = cached
    expected = [(LESSON_A, "lesson-a"), (LESSON_B, "lesson-b"), (RUSSIAN_TEXT, "russian")]
    for index, (text, identifier) in enumerate(expected, 1):
        result = read(client, method, view="learning", section=index)
        assert result["status"] == "ready" and result["total_sections"] == 3
        assert result["spoken_text"].endswith(text)
        assert f"第{index}段，共3段。" in result["spoken_text"]
        assert result["next_section"] == (index + 1 if index < 3 else None)
        assert source_ids(result, "sources") == {identifier}
        assert source_ids(result) == {"lesson-a", "lesson-b", "russian"}
        for full_index, (full_text, _) in enumerate(expected, 1):
            assert f"第{full_index}段，共3段。" + full_text in result["full_spoken_text"]
        for hidden in (NEWS_TEXT, WORK_TEXT, LESSON_SUMMARY, RUSSIAN_SUMMARY, PRIVATE_TITLE, PRIVATE_WARNING):
            assert hidden not in json.dumps(result, ensure_ascii=False)
        assert result["source_dates"] == [TODAY] and result["warnings"] == []
    finished = read(client, method, view="learning", section=4)
    assert finished["status"] == "finished" and finished["sources"] == []
    assert source_ids(finished) == {"lesson-a", "lesson-b", "russian"}
    topic = read(client, method, view="learning", topic="练习")
    assert topic["total_sections"] == 1 and topic["next_section"] is None
    assert "第1段，共1段。" + LESSON_B in topic["full_spoken_text"]
    assert source_ids(topic) == source_ids(topic, "sources") == {"lesson-b"}


class UnreadableCache(dict):
    def __iter__(self):
        raise AssertionError("Disabled work must not inspect cached dates")

    def get(self, *args, **kwargs):
        raise AssertionError("Disabled work must not inspect cached editions")


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("date,expected_date", [("today", TODAY), ("2026-10-25", "2026-10-25"), ("latest", None)])
def test_disabled_work_returns_before_accessing_any_cache(setup, method, date, expected_date):
    client, store = setup
    store.state["editions"] = UnreadableCache({TODAY: {"title": PRIVATE_TITLE, "warnings": [PRIVATE_WARNING]}})
    result = read(client, method, date=date, view="work")
    assert result["status"] == "disabled" and result["date"] == expected_date
    assert result["latest_date"] is None
    assert_empty_content(result)
    assert PRIVATE_TITLE not in json.dumps(result, ensure_ascii=False)
    assert PRIVATE_WARNING not in json.dumps(result, ensure_ascii=False)


def test_work_switch_hides_existing_cached_work_immediately_in_all_response_fields(cached):
    client, store = cached
    accepted = deepcopy(store.state["editions"][TODAY])
    set_preferences(store, work_enabled=True)
    for view in ("daily", "work", "full"):
        enabled = read(client, view=view)
        assert enabled["status"] == "ready"
        assert WORK_TEXT in enabled["full_spoken_text"]
        assert "work" in source_ids(enabled)
    full = read(client, view="full")
    assert full["title"] == PRIVATE_TITLE and full["warnings"] == [PRIVATE_WARNING]
    set_preferences(store, work_enabled=False)
    for view in ("daily", "full"):
        disabled = read(client, view=view)
        assert disabled["status"] == "ready"
        serialized = json.dumps(disabled, ensure_ascii=False)
        for private in (WORK_TEXT, PRIVATE_TITLE, PRIVATE_WARNING, WORK, "https://example.com/synthetic/work"):
            assert private not in serialized
        assert "work" not in source_ids(disabled) | source_ids(disabled, "sources")
        assert disabled["source_dates"] == [TODAY] and disabled["warnings"] == []
    assert read(client, view="work")["status"] == "disabled"
    assert store.state["editions"][TODAY] == accepted


def test_disabling_the_only_cached_work_source_leaves_daily_and_full_without_cache_metadata(setup):
    client, store = setup
    set_preferences(store, categories={WORK: "work"}, work_enabled=True)
    publish_cache(client, [("work", WORK)], [section(WORK_TEXT, "work")])
    assert read(client, view="full")["title"] == PRIVATE_TITLE
    set_preferences(store, work_enabled=False)
    for view in ("daily", "full"):
        result = read(client, view=view)
        assert result["status"] == "view_missing"
        assert_empty_content(result)
        assert WORK_TEXT not in json.dumps(result, ensure_ascii=False)


def test_empty_learning_view_and_unmatched_topic_are_distinct(cached, setup):
    client, _ = cached
    missing_topic = read(client, view="learning", topic="不存在的合成主题")
    assert missing_topic["status"] == "topic_missing"
    assert_empty_content(missing_topic, empty_title=False, no_cache_metadata=False)
    assert missing_topic["warnings"] == []
    # Reclassify the two learning sources without regenerating their cache.
    _, store = setup
    set_preferences(store, categories={LESSON: "news", RUSSIAN: "news"})
    missing_view = read(client, view="learning")
    assert missing_view["status"] == "view_missing"
    assert_empty_content(missing_view)


@pytest.mark.parametrize("missing", [True, False], ids=["missing-field", "empty-list"])
def test_legacy_cache_uses_fixed_learning_prompts_and_never_truncated_lesson_text(cached, missing):
    client, store = cached
    if missing:
        store.state["editions"][TODAY].pop("learning_summaries")
    else:
        store.state["editions"][TODAY]["learning_summaries"] = []
    daily = read(client)
    assert daily["status"] == "ready" and daily["total_sections"] == 3
    for source in (LESSON, RUSSIAN):
        expected = f"今天的学习材料是《{source}》，可说今天应该学什么听取完整内容。"
        assert daily["full_spoken_text"].count(expected) == 1
    for text in (LESSON_A[:100], LESSON_B, RUSSIAN_TEXT, LESSON_SUMMARY, RUSSIAN_SUMMARY):
        assert text not in daily["full_spoken_text"]
    assert source_ids(read(client, section=2), "sources") == {"lesson-a", "lesson-b"}
    learning = read(client, view="learning")
    for text in (LESSON_A, LESSON_B, RUSSIAN_TEXT):
        assert text in learning["full_spoken_text"]


@pytest.mark.parametrize("category", ["news", "work"])
def test_current_category_overrides_previously_cached_learning_metadata(cached, category):
    client, store = cached
    accepted = deepcopy(store.state["editions"][TODAY])
    set_preferences(store, categories={LESSON: category})
    daily = read(client)
    assert LESSON_SUMMARY not in daily["full_spoken_text"]
    learning = read(client, view="learning")
    assert source_ids(learning) == {"russian"}
    assert LESSON_A not in learning["full_spoken_text"] and LESSON_B not in learning["full_spoken_text"]
    if category == "news":
        assert LESSON_A in daily["full_spoken_text"] and LESSON_B in daily["full_spoken_text"]
    else:
        assert LESSON_A not in daily["full_spoken_text"] and LESSON_B not in daily["full_spoken_text"]
        assert not {"lesson-a", "lesson-b"} & source_ids(daily)
    assert store.state["editions"][TODAY] == accepted


def test_news_reclassified_as_learning_uses_fixed_daily_prompt_but_full_learning_text(cached):
    client, store = cached
    set_preferences(store, categories={NEWS: "learning"})
    daily = read(client)
    assert NEWS_TEXT not in daily["full_spoken_text"]
    assert f"今天的学习材料是《{NEWS}》，可说今天应该学什么听取完整内容。" in daily["full_spoken_text"]
    learning = read(client, view="learning")
    assert NEWS_TEXT in learning["full_spoken_text"]
    assert source_ids(learning) == {"news", "lesson-a", "lesson-b", "russian"}


@pytest.mark.parametrize("view", ["daily", "learning", "full"])
def test_unknown_cached_source_id_is_unavailable_without_private_output(cached, view):
    client, store = cached
    store.state["editions"][TODAY]["sections"][0]["source_ids"] = ["unknown-private-id"]
    result = read(client, view=view)
    assert result["status"] == "unavailable"
    assert_empty_content(result)
    for text in (NEWS_TEXT, LESSON_A, PRIVATE_TITLE, PRIVATE_WARNING, "unknown-private-id"):
        assert text not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("corruption", ["mixed", "duplicate", "unknown_subject", "summary_subset"])
def test_other_corrupted_provenance_fails_closed_through_http(cached, corruption):
    client, store = cached
    edition = store.state["editions"][TODAY]
    if corruption == "mixed":
        edition["sections"][0]["source_ids"] = ["news", "lesson-a"]
    elif corruption == "duplicate":
        edition["sections"][0]["source_ids"] = ["news", "news"]
    elif corruption == "unknown_subject":
        store.state["messages"]["news"]["subject"] = f"[每日早报][未授权的合成来源][{TODAY}]"
    else:
        lesson_summary = next(summary for summary in edition["learning_summaries"] if "lesson-a" in summary["source_ids"])
        lesson_summary["source_ids"] = ["lesson-a"]
    result = read(client)
    assert result["status"] == "unavailable"
    assert_empty_content(result)
    assert PRIVATE_TITLE not in json.dumps(result, ensure_ascii=False)
    assert PRIVATE_WARNING not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("view", ["everything", "raw", "Learning", ""])
def test_invalid_view_never_falls_back_to_full_cached_content(cached, method, view):
    client, _ = cached
    if method == "GET":
        response = client.get(PREFIX + "/read", headers=auth("read"), params={"view": view})
    else:
        response = client.post(PREFIX + "/read", headers=auth("read"), json={"view": view})
    assert response.status_code == 422
    for text in (NEWS_TEXT, LESSON_A, WORK_TEXT, PRIVATE_TITLE, PRIVATE_WARNING):
        assert text not in response.text


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("view", ["daily", "learning", "work", "full"])
def test_three_parameter_tool_alias_matches_explicit_view(cached, method, view):
    client, store = cached
    before = deepcopy(store.state)
    expected = read(client, method, date="latest", view=view, section=2)
    actual = read(client, method, date="latest", topic="__view_" + view, section=2)
    assert actual == expected
    assert actual["view"] == view and actual["topic"] == "all"
    assert store.state == before


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("explicit_view,alias_view", [("learning", "full"), ("work", "daily"), ("full", "learning")])
def test_conflicting_explicit_view_and_reserved_topic_are_rejected(cached, method, explicit_view, alias_view):
    client, store = cached
    parameters = {"view": explicit_view, "topic": "__view_" + alias_view}
    before = deepcopy(store.state)
    response = (client.get(PREFIX + "/read", headers=auth("read"), params=parameters)
                if method == "GET" else client.post(PREFIX + "/read", headers=auth("read"), json=parameters))
    assert response.status_code == 422
    assert store.state == before
    for text in (NEWS_TEXT, LESSON_A, WORK_TEXT, PRIVATE_TITLE, PRIVATE_WARNING):
        assert text not in response.text


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("explicit_view,alias_view", [("learning", "learning"), ("daily", "learning"), ("work", "work"), ("full", "full")])
def test_matching_or_default_explicit_view_accepts_reserved_topic(cached, method, explicit_view, alias_view):
    client, _ = cached
    actual = read(client, method, view=explicit_view, topic="__view_" + alias_view)
    assert actual == read(client, method, view=alias_view)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("topic", ["定义", "__view_unknown", "__view_Learning", " __view_learning", "__view_learning "])
def test_only_exact_reserved_topic_values_are_normalized(cached, method, topic):
    client, _ = cached
    result = read(client, method, view="learning", topic=topic)
    assert result["view"] == "learning" and result["topic"] == topic
    if topic == "定义":
        assert result["status"] == "ready" and result["total_sections"] == 1
        assert result["spoken_text"].endswith(LESSON_A)
    else:
        assert result["status"] == "topic_missing" and result["full_spoken_text"] == ""
