from copy import deepcopy
import json

import pytest

from briefing.source_policy import SOURCE_ORDER, SOURCE_POLICY, load_source_policy


def test_canonical_order_matches_the_selected_daily_weekly_then_conditional_sources():
    assert list(SOURCE_ORDER) == [
        "每日资讯", "市场观察", "每日知识练习", "生活提示", "外语练习",
        "技术周报", "每周学习复盘",
        "项目邮件回复", "客户邮件回复", "活动动态",
    ]
    assert list(SOURCE_ORDER.values()) == list(range(10))
    assert [source["kind"] for source in SOURCE_POLICY["sources"]] == ["daily"] * 5 + ["weekly"] * 2 + ["conditional"] * 3
    assert [source["event_type"] for source in SOURCE_POLICY["sources"]] == ["定时结果"] * 7 + ["新回复", "新回复", "新信息"]
    assert {source["name"]: source["narration_detail"] for source in SOURCE_POLICY["sources"]} == {
        "每日资讯": "standard", "市场观察": "standard", "每日知识练习": "detailed",
        "生活提示": "simple", "外语练习": "detailed",
        "技术周报": "standard", "每周学习复盘": "standard",
        "项目邮件回复": "standard", "客户邮件回复": "standard", "活动动态": "standard",
    }


@pytest.mark.parametrize("mutate", [
    lambda policy: policy.update(version=2),
    lambda policy: policy.update(version=True),
    lambda policy: policy.update(sources=[]),
    lambda policy: policy["sources"].append(deepcopy(policy["sources"][0])),
    lambda policy: policy["sources"][0].update(event_type="新回复"),
    lambda policy: policy["sources"][0].update(name="非法[名称]"),
])
def test_invalid_or_unknown_policy_fails_closed(tmp_path, mutate):
    policy = deepcopy(SOURCE_POLICY)
    mutate(policy)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="source_policy"):
        load_source_policy(path)


@pytest.mark.parametrize("kind,event_type", [
    ("weekly", "新回复"), ("weekly", "新信息"), ("weekly", ""),
    ("monthly", "定时结果"), ("conditional", "定时结果"),
])
def test_unknown_frequency_and_invalid_frequency_event_combinations_fail_closed(tmp_path, kind, event_type):
    policy = deepcopy(SOURCE_POLICY)
    policy["sources"][0].update(kind=kind, event_type=event_type)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="source_policy"):
        load_source_policy(path)


@pytest.mark.parametrize("narration_detail", ["simple", "standard", "detailed"])
def test_all_narration_details_are_accepted_without_changing_the_policy(tmp_path, narration_detail):
    policy = deepcopy(SOURCE_POLICY)
    policy["sources"][0]["narration_detail"] = narration_detail
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    assert load_source_policy(path) == policy


@pytest.mark.parametrize("narration_detail", ["", "brief", "Detailed", " standard ", None, True, 1, [], {}])
def test_invalid_narration_detail_fails_closed(tmp_path, narration_detail):
    policy = deepcopy(SOURCE_POLICY)
    policy["sources"][-1]["narration_detail"] = narration_detail
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="source_policy"):
        load_source_policy(path)


@pytest.mark.parametrize("source_index", [0, -1])
def test_missing_narration_detail_does_not_silently_default(tmp_path, source_index):
    policy = deepcopy(SOURCE_POLICY)
    del policy["sources"][source_index]["narration_detail"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="source_policy"):
        load_source_policy(path)


def test_source_policy_rejects_fields_beyond_the_four_declared_fields(tmp_path):
    policy = deepcopy(SOURCE_POLICY)
    policy["sources"][-1]["narration_detail_override"] = "simple"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="source_policy"):
        load_source_policy(path)
