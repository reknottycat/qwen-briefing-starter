"""The JSON file is the canonical source, event, order and narration policy."""
import json
import os
from pathlib import Path

NARRATION_DETAILS = ("simple", "standard", "detailed")


def load_source_policy(path: Path | None = None) -> dict:
    if path is None:
        configured = os.environ.get("BRIEFING_SOURCE_POLICY_PATH")
        path = Path(configured) if configured else Path(__file__).with_name("source_policy.json")
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or set(policy) != {"version", "sources"} or type(policy["version"]) is not int or policy["version"] != 1:
        raise ValueError("unsupported_source_policy")
    if not isinstance(policy["sources"], list) or not policy["sources"]:
        raise ValueError("invalid_source_policy")
    names = set()
    for source in policy["sources"]:
        if not isinstance(source, dict) or set(source) != {"name", "kind", "event_type", "narration_detail"}:
            raise ValueError("invalid_source_policy")
        name = source["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or name != name.strip() or any(char in name for char in "[]\r\n") or name in names:
            raise ValueError("invalid_source_policy")
        if not ((source["kind"] in ("daily", "weekly") and source["event_type"] == "定时结果") or
                (source["kind"] == "conditional" and source["event_type"] in ("新回复", "新信息"))):
            raise ValueError("invalid_source_policy")
        if source["narration_detail"] not in NARRATION_DETAILS:
            raise ValueError("invalid_source_policy")
        names.add(name)
    return policy


SOURCE_POLICY = load_source_policy()
SOURCE_RULES = {source["name"]: source for source in SOURCE_POLICY["sources"]}
SOURCE_ORDER = {source["name"]: index for index, source in enumerate(SOURCE_POLICY["sources"])}


def validate_event_marker(body: str, source_name: str) -> None:
    lines = body.splitlines()
    marker = "早报事件类型：" + SOURCE_RULES[source_name]["event_type"]
    if not lines or lines[0] != marker:
        raise ValueError("report_event_marker_required")
    if not any(line.strip() for line in lines[1:]):
        raise ValueError("report_event_body_required")
