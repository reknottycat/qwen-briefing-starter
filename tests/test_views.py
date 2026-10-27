from copy import deepcopy

import pytest

from briefing.preferences import default_preferences
from briefing.views import select_view


NEWS = "市场观察"
LEARNING = "每日知识练习"
RUSSIAN = "外语练习"
WORK = "项目邮件回复"


def message(identifier, source):
    return {"id": identifier, "subject": f"[每日早报][{source}][2026-10-27]"}


def section(text, *source_ids):
    return {"title": "合成标题", "topic": "合成主题", "text": text, "source_ids": list(source_ids)}


@pytest.fixture
def prepared():
    preferences = default_preferences()
    preferences["sources"][WORK]["category"] = "work"
    messages = [message("work", WORK), message("lesson-b", LEARNING),
                message("news", NEWS), message("lesson-a", LEARNING), message("russian", RUSSIAN)]
    edition = {
        "sections": [section("工作私密正文。", "work"), section("课程第一段完整定义与原理。", "lesson-a"),
                     section("新闻事实。", "news"), section("课程第二段完整练习。", "lesson-b"),
                     section("俄语完整原句与解释。", "russian")],
        "learning_summaries": [section("俄语学习概览。", "russian"),
                               section("学科学习概览。", "lesson-b", "lesson-a")],
    }
    return edition, messages, preferences


def texts(sections):
    return [item["text"] for item in sections]


def test_daily_uses_one_short_overview_per_learning_source_without_full_lessons(prepared):
    selected = select_view(*prepared, "daily")
    assert texts(selected) == ["新闻事实。", "学科学习概览。", "俄语学习概览。"]
    assert selected[1]["source_ids"] == ["lesson-b", "lesson-a"]
    assert all(len(item["text"]) <= 250 for item in selected[1:])


def test_learning_returns_complete_learning_in_source_and_internal_order(prepared):
    assert texts(select_view(*prepared, "learning")) == [
        "课程第一段完整定义与原理。", "课程第二段完整练习。", "俄语完整原句与解释。"]


@pytest.mark.parametrize("view", ["daily", "learning", "work", "full"])
def test_work_off_excludes_old_work_even_with_forged_labels(prepared, view):
    edition, messages, preferences = prepared
    edition["sections"][0].update(title=NEWS, topic="学习")
    # Public section metadata cannot assign source identity or a category.
    selected = select_view(edition, messages, preferences, view)
    assert all("work" not in item["source_ids"] for item in selected)
    assert "工作私密正文。" not in texts(selected)


@pytest.mark.parametrize("view", ["daily", "work", "full"])
def test_work_on_returns_existing_work_and_does_not_invent_missing_data(prepared, view):
    edition, messages, preferences = prepared
    preferences["work_enabled"] = True
    assert "工作私密正文。" in texts(select_view(edition, messages, preferences, view))
    edition["sections"] = [item for item in edition["sections"] if "work" not in item["source_ids"]]
    assert "工作私密正文。" not in texts(select_view(edition, messages, preferences, view))
    assert select_view(edition, messages, preferences, "work") == []


def test_full_returns_complete_news_and_learning_without_disabled_work(prepared):
    assert texts(select_view(*prepared, "full")) == ["新闻事实。", "课程第一段完整定义与原理。",
                                                   "课程第二段完整练习。", "俄语完整原句与解释。"]


@pytest.mark.parametrize("missing", [True, False])
def test_legacy_daily_explicitly_points_to_lessons_without_truncating_them(prepared, missing):
    edition, messages, preferences = prepared
    if missing:
        edition.pop("learning_summaries")
    else:
        edition["learning_summaries"] = []
    selected = select_view(edition, messages, preferences, "daily")
    assert texts(selected) == ["新闻事实。", f"今天的学习材料是《{LEARNING}》，可说今天应该学什么听取完整内容。",
                              f"今天的学习材料是《{RUSSIAN}》，可说今天应该学什么听取完整内容。"]
    assert selected[1]["source_ids"] == ["lesson-a", "lesson-b"]
    assert len(selected) == 3
    assert "课程第一段完整定义与原理。" in texts(select_view(edition, messages, preferences, "learning"))


@pytest.mark.parametrize("category", ["news", "work"])
def test_current_category_prevents_stale_learning_summary_routing(prepared, category):
    edition, messages, preferences = prepared
    preferences["sources"][LEARNING]["category"] = category
    selected = select_view(edition, messages, preferences, "daily")
    assert "学科学习概览。" not in texts(selected)
    assert "课程第一段完整定义与原理。" not in texts(select_view(edition, messages, preferences, "learning"))
    if category == "news":
        assert "课程第一段完整定义与原理。" in texts(selected)
    else:
        assert all(not set(item["source_ids"]) & {"lesson-a", "lesson-b"} for item in selected)


def test_news_reclassified_as_learning_gets_explicit_fallback(prepared):
    edition, messages, preferences = prepared
    preferences["sources"][NEWS]["category"] = "learning"
    selected = select_view(edition, messages, preferences, "daily")
    assert selected[0]["text"] == f"今天的学习材料是《{NEWS}》，可说今天应该学什么听取完整内容。"
    assert "新闻事实。" in texts(select_view(edition, messages, preferences, "learning"))


def test_registered_learning_without_cached_sections_does_not_create_material(prepared):
    edition, messages, preferences = prepared
    edition["sections"] = [item for item in edition["sections"] if "russian" not in item["source_ids"]]
    assert all("russian" not in item["source_ids"] for item in select_view(edition, messages, preferences, "daily"))


def test_new_mail_does_not_invalidate_previous_good_cached_summary(prepared):
    edition, messages, preferences = prepared
    messages.append(message("new-lesson", LEARNING))
    assert "学科学习概览。" in texts(select_view(edition, messages, preferences, "daily"))


@pytest.mark.parametrize("source_ids", [[], ["unknown"], ["lesson-a", "news"], ["lesson-a", "lesson-a"]])
def test_invalid_or_mixed_section_identity_fails_closed(prepared, source_ids):
    edition, messages, preferences = prepared
    edition["sections"][1]["source_ids"] = source_ids
    with pytest.raises(ValueError, match="^invalid_view_sources$"):
        select_view(edition, messages, preferences, "daily")


@pytest.mark.parametrize("mutation", ["unknown_subject", "duplicate_id", "missing_subject"])
def test_untrusted_message_identity_fails_closed(prepared, mutation):
    edition, messages, preferences = prepared
    if mutation == "unknown_subject":
        messages[1]["subject"] = "[每日早报][未登记来源][2026-10-27]"
    elif mutation == "duplicate_id":
        messages.append(message("lesson-a", WORK))
    else:
        messages[1].pop("subject")
    with pytest.raises(ValueError, match="^invalid_view_sources$"):
        select_view(edition, messages, preferences, "full")


@pytest.mark.parametrize("mutation", ["long", "duplicate", "mixed", "unknown", "subset"])
def test_invalid_learning_summary_fails_closed(prepared, mutation):
    edition, messages, preferences = prepared
    summary = edition["learning_summaries"][1]
    if mutation == "long":
        summary["text"] = "学" * 251
    elif mutation == "duplicate":
        edition["learning_summaries"].append(deepcopy(summary))
    elif mutation == "mixed":
        summary["source_ids"].append("news")
    elif mutation == "unknown":
        summary["source_ids"].append("unknown")
    else:
        summary["source_ids"] = ["lesson-a"]
    with pytest.raises(ValueError, match="^invalid_view_sources$"):
        select_view(edition, messages, preferences, "daily")


@pytest.mark.parametrize("work_enabled", ["false", 1, None])
def test_work_switch_must_be_a_valid_boolean(prepared, work_enabled):
    edition, messages, preferences = prepared
    preferences["work_enabled"] = work_enabled
    with pytest.raises(ValueError, match="^invalid_view_sources$"):
        select_view(edition, messages, preferences, "full")


def test_invalid_view_does_not_fall_back_to_full(prepared):
    with pytest.raises(ValueError, match="^invalid_view$"):
        select_view(*prepared, "everything")


@pytest.mark.parametrize("view", ["daily", "learning", "full"])
def test_select_view_does_not_mutate_or_alias_inputs(prepared, view):
    before = deepcopy(prepared)
    selected = select_view(*prepared, view)
    assert prepared == before
    selected[0]["text"] = "changed"
    selected[0]["source_ids"].append("changed")
    assert prepared == before
