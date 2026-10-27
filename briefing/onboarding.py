"""Prepare a first installation from reviewed tasks, without editing any scheduler.

Confirmation flags record an operator's decision; they are not authentication or
proof of human approval. Agents must present each proposal and obtain the user's
confirmation before invoking the corresponding command. Files are immutable:
each stage writes a new file, and any edit invalidates downstream confirmations.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlsplit

MAX_FILE_BYTES = 1_000_000
GENERATORS = {"codex", "pi"}
TASK_KEYS = {"id", "name", "cadence", "enabled", "monitoring", "evidence"}
CHOICE_KEYS = {"task_id", "category", "mode", "narration_detail"}
PLAN_KEYS = {"version", "inventory_digest", "tasks", "excluded", "work_enabled"}
PLAN_OPTIONAL = {"task_confirmation", "server", "server_confirmation"}
PLANNED_TASK_KEYS = {"task_id", "source_name", "category", "mode", "narration_detail", "evidence"}
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


class SetupError(ValueError):
    """An actionable, bounded error code that never includes input values."""


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown argument values, which may include a
        # mistakenly supplied password or key. Only our bounded codes are safe.
        if not re.fullmatch(r"[a-z0-9_]+", message):
            message = "invalid_arguments_use_help_and_do_not_pass_credentials"
        super().error(message)


def fail(code: str) -> None:
    raise SetupError(code)


def keys(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        fail("invalid_fields")


def text(value, maximum=200):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip()
            or any(ord(char) < 32 for char in value)):
        fail("invalid_text")
    return value


def source_name(value):
    text(value, 80)
    if any(char in value for char in "[]"):
        fail("invalid_source_name")
    return value


def read_json(path: Path):
    if path.stat().st_size > MAX_FILE_BYTES:
        fail("input_too_large")
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def write_new(path: Path, value, *, raw=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL also protects against an existing symlink and concurrent writers.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(value if raw else json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_inventory(value):
    keys(value, {"version", "tasks"})
    if type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["tasks"], list):
        fail("invalid_inventory")
    if len(value["tasks"]) > 1000:
        fail("too_many_tasks")
    seen = set()
    for task in value["tasks"]:
        keys(task, TASK_KEYS)
        task_id = text(task["id"])
        source_name(task["name"])
        text(task["evidence"], 400)
        if (task_id in seen or task["cadence"] not in {"daily", "weekly", "other", "unknown"}
                or type(task["enabled"]) is not bool
                or not (task["monitoring"] is None or type(task["monitoring"]) is bool)):
            fail("invalid_inventory_task")
        seen.add(task_id)
    return value


def cadence_from_rrule(value):
    if not isinstance(value, str):
        return "unknown"
    lines = value.strip().splitlines()
    rule = next((line[6:] for line in lines if line.startswith("RRULE:")), value.strip())
    parts = {}
    for item in rule.split(";"):
        pair = item.split("=", 1)
        if len(pair) != 2 or pair[0] in parts:
            return "unknown"
        parts[pair[0]] = pair[1]
    if parts.get("FREQ") == "WEEKLY":
        return "weekly"
    if parts.get("FREQ") == "DAILY":
        # A bounded recognizer, not a recurrence engine. Unknown restrictions,
        # end conditions and malformed times need explicit scheduler review.
        supported = {"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYSECOND", "WKST"}
        if parts.keys() - supported:
            return "unknown"
        if parts.get("INTERVAL", "1") != "1":
            return "other"
        for key, maximum in (("BYHOUR", 23), ("BYMINUTE", 59), ("BYSECOND", 60)):
            if key in parts and any(not re.fullmatch(r"[0-9]{1,2}", number) or int(number) > maximum
                                    for number in parts[key].split(",")):
                return "unknown"
        if "WKST" in parts and parts["WKST"] not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}:
            return "unknown"
        return "daily"
    return "other" if "FREQ" in parts else "unknown"


def discover_codex(directory: Path):
    if not directory.is_dir():
        fail("automation_directory_not_found")
    tasks = []
    for path in sorted(directory.glob("*/automation.toml")):
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            fail("unsafe_automation_file")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        tasks.append({"id": raw.get("id", path.parent.name), "name": raw.get("name", path.parent.name),
                      "cadence": cadence_from_rrule(raw.get("rrule")),
                      "enabled": raw.get("status") == "ACTIVE", "monitoring": None,
                      "evidence": "Review the original task purpose; schedule alone cannot establish non-monitoring."})
    # Prompts and credentials are intentionally not copied to inventory output.
    return validate_inventory({"version": 1, "tasks": tasks})


def validate_choice(choice):
    keys(choice, CHOICE_KEYS, {"monitoring_review"})
    text(choice["task_id"])
    if (choice["category"] not in {"news", "learning", "work"}
            or choice["mode"] not in {"raw", "processed", "podcast"}
            or choice["narration_detail"] not in {"simple", "standard", "detailed"}):
        fail("invalid_task_selection")


def exclusion(task):
    if not task["enabled"]:
        return "inactive"
    if task["cadence"] != "daily":
        return "not_daily"
    if task["monitoring"] is True:
        return "monitoring"
    if task["monitoring"] is None:
        return "monitoring_review_required"
    return "not_selected"


def make_plan(inventory, selection):
    validate_inventory(inventory)
    keys(selection, {"version", "sources"}, {"work_enabled"})
    if (type(selection["version"]) is not int or selection["version"] != 1
            or not isinstance(selection["sources"], list) or not 1 <= len(selection["sources"]) <= 32
            or type(selection.get("work_enabled", False)) is not bool):
        fail("invalid_selection")
    by_id = {item["id"]: item for item in inventory["tasks"]}
    selected = set()
    names = set()
    tasks = []
    for choice in selection["sources"]:
        validate_choice(choice)
        task_id = choice["task_id"]
        if task_id not in by_id or task_id in selected:
            fail("unknown_or_duplicate_task")
        task = deepcopy(by_id[task_id])
        if "monitoring_review" in choice:
            review = choice["monitoring_review"]
            keys(review, {"monitoring", "evidence"})
            if type(review["monitoring"]) is not bool:
                fail("invalid_monitoring_review")
            text(review["evidence"], 400)
            if task["monitoring"] is not None and task["monitoring"] != review["monitoring"]:
                fail("monitoring_review_conflict")
            task.update(review)
        if exclusion(task) != "not_selected":
            fail("selected_task_is_not_active_daily_non_monitoring")
        if task["name"] in names:
            fail("duplicate_source_name")
        names.add(task["name"])
        selected.add(task_id)
        tasks.append({"task_id": task_id, "source_name": task["name"],
                      **{field: choice[field] for field in ("category", "mode", "narration_detail")},
                      "evidence": task["evidence"]})
    return {"version": 1, "inventory_digest": digest(inventory), "tasks": tasks,
            "excluded": [{"task_id": task["id"], "reason": exclusion(task)}
                         for task in inventory["tasks"] if task["id"] not in selected],
            "work_enabled": selection.get("work_enabled", False)}


def task_digest(plan):
    return digest({key: plan[key] for key in PLAN_KEYS})


def server_digest(plan, server):
    return digest({"task_digest": task_digest(plan), "server": server})


def validate_server(value):
    keys(value, {"deployment", "base_url", "ssh_alias", "generator"}, {"provider", "model", "llm_base_url"})
    if value["deployment"] not in {"docker", "linux"} or value["generator"] not in GENERATORS:
        fail("invalid_server_choice")
    if value["deployment"] == "docker" and value["generator"] == "codex":
        fail("codex_requires_linux_choose_pi_for_docker")
    if not isinstance(value["ssh_alias"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value["ssh_alias"]):
        fail("ssh_alias_required_without_credentials_or_options")
    for key in ("base_url", "llm_base_url"):
        if key not in value:
            continue
        url = text(value[key], 500)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(char.isspace() for char in url)):
            fail("https_url_required_without_credentials_query_or_fragment")
        parsed.port
    if urlsplit(value["base_url"]).path.rstrip("/") != "/briefing/v1":
        fail("base_url_must_end_in_briefing_v1")
    if "model" in value:
        text(value["model"])
    if "provider" in value:
        if not isinstance(value["provider"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value["provider"]):
            fail("invalid_provider_identifier")
    if value["generator"] == "pi" and not {"provider", "model"} <= value.keys():
        fail("pi_provider_and_model_required")
    return value


def load_plan(path: Path):
    value = read_json(path)
    keys(value, PLAN_KEYS, PLAN_OPTIONAL)
    if (type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["inventory_digest"], str) or not HEX_DIGEST.fullmatch(value["inventory_digest"])
            or type(value["work_enabled"]) is not bool or not isinstance(value["tasks"], list)
            or not 1 <= len(value["tasks"]) <= 32 or not isinstance(value["excluded"], list)):
        fail("invalid_plan")
    names, task_ids = set(), set()
    for task in value["tasks"]:
        keys(task, PLANNED_TASK_KEYS)
        source_name(task["source_name"])
        text(task["evidence"], 400)
        validate_choice({key: task[key] for key in CHOICE_KEYS})
        if task["source_name"] in names or task["task_id"] in task_ids:
            fail("duplicate_plan_task")
        names.add(task["source_name"])
        task_ids.add(task["task_id"])
    for item in value["excluded"]:
        keys(item, {"task_id", "reason"})
        text(item["task_id"])
        if item["reason"] not in {"inactive", "not_daily", "monitoring", "monitoring_review_required", "not_selected"}:
            fail("invalid_exclusion")
    if "task_confirmation" in value:
        check_confirmation(value["task_confirmation"], task_digest(value), "task_confirmation_invalidated")
    if "server" in value:
        validate_server(value["server"])
        if "task_confirmation" not in value:
            fail("tasks_must_be_confirmed_first")
    if "server_confirmation" in value:
        if "server" not in value or "task_confirmation" not in value:
            fail("server_confirmation_out_of_order")
        check_confirmation(value["server_confirmation"], server_digest(value, value["server"]),
                           "server_confirmation_invalidated")
    return value


def check_confirmation(value, expected, error):
    keys(value, {"digest", "confirmed"})
    if value["confirmed"] is not True or value["digest"] != expected:
        fail(error)


def review(path: Path, server_path: Path | None = None):
    plan = load_plan(path)
    result = {"task_digest": task_digest(plan), "tasks_confirmed": "task_confirmation" in plan,
              "server_confirmed": "server_confirmation" in plan, "tasks": plan["tasks"],
              "excluded": plan["excluded"], "work_enabled": plan["work_enabled"]}
    if server_path is not None:
        if "task_confirmation" not in plan:
            fail("tasks_must_be_confirmed_first")
        server = validate_server(read_json(server_path))
        result.update(server=server, server_digest=server_digest(plan, server),
                      server_confirmed=plan.get("server_confirmation", {}).get("digest") == server_digest(plan, server))
    elif "server" in plan:
        result.update(server=plan["server"], server_digest=server_digest(plan, plan["server"]))
    return result


def export_setup(plan, output: Path):
    if "task_confirmation" not in plan or "server_confirmation" not in plan:
        fail("both_confirmations_required_before_qwen_handoff")
    policy = {"version": 1, "sources": [{"name": task["source_name"], "kind": "daily",
               "event_type": "定时结果", "narration_detail": task["narration_detail"]} for task in plan["tasks"]]}
    preferences = {"revision": 1, "format": "mixed", "work_enabled": plan["work_enabled"], "sources": {
        task["source_name"]: {"category": task["category"], "mode": task["mode"],
                              "narration_detail": task["narration_detail"], "note": ""} for task in plan["tasks"]}}
    manifest = {"version": 1, "task_digest": task_digest(plan),
                "server_digest": server_digest(plan, plan["server"]), "server": plan["server"],
                "files": {"source_policy": "source-policy.json", "initial_preferences": "initial-preferences.json"},
                "scheduler_modified": False, "server_deployed": False,
                "qwen_handoff": {"ready_for_configuration": True, "device_verified": False,
                                 "requires": ["backend_https_verified", "qwen_developer_login", "http_tool_plugin", "skill", "device_test"]}}
    instructions = ["# 已选任务的邮件输出约定（待应用）", "",
        "这份文件不会修改定时任务。先核对原任务实际支持的投递方式，按用户确认的范围更新；保留原任务正文、时间与生成内容。",
        "使用运行日期替换 YYYY-MM-DD，收件邮箱由用户在其任务平台设置，不写入此配置包。", "",
        "邮件正文首行为：早报事件类型：定时结果。接着换行，保留本次任务的完整结果。", "",
        "按以下任务顺序设置精确主题：", ""]
    for task in plan["tasks"]:
        instructions.extend(["~~~text", f"[每日早报][{task['source_name']}][YYYY-MM-DD]", "~~~", ""])
    # mkdir without exist_ok refuses existing installations, including empty ones.
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_new(output / "source-policy.json", policy)
    write_new(output / "initial-preferences.json", preferences)
    write_new(output / "setup-manifest.json", manifest)
    write_new(output / "mail-delivery.md", "\n".join(instructions), raw=True)


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Read existing task metadata; never modify schedules")
    source = discover.add_mutually_exclusive_group(required=True)
    source.add_argument("--codex-dir", type=Path)
    source.add_argument("--tasks", type=Path, help="Normalized task inventory exported from the user's scheduler")
    discover.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("plan", help="Propose selected daily non-monitoring tasks and categories")
    plan.add_argument("--inventory", type=Path, required=True)
    plan.add_argument("--selection", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    status = commands.add_parser("status", help="Show the exact proposal and digest for user review")
    status.add_argument("--plan", type=Path, required=True)
    status.add_argument("--server", type=Path)
    for name in ("confirm-tasks", "confirm-server"):
        confirm = commands.add_parser(name, help="Record a user's prior confirmation of the shown digest")
        confirm.add_argument("--plan", type=Path, required=True)
        if name == "confirm-server":
            confirm.add_argument("--server", type=Path, required=True)
        confirm.add_argument("--digest", required=True)
        confirm.add_argument("--confirm", action="store_true", required=True)
        confirm.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export", help="Create first-install configuration after both confirmations")
    export.add_argument("--plan", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            value = discover_codex(args.codex_dir) if args.codex_dir else validate_inventory(read_json(args.tasks))
            write_new(args.output, value)
        elif args.command == "plan":
            write_new(args.output, make_plan(read_json(args.inventory), read_json(args.selection)))
        elif args.command == "status":
            print(json.dumps(review(args.plan, args.server), ensure_ascii=False, indent=2))
            return 0
        elif args.command == "confirm-tasks":
            value = load_plan(args.plan)
            if args.digest != task_digest(value):
                fail("task_digest_does_not_match_reviewed_proposal")
            if "task_confirmation" in value:
                fail("tasks_already_confirmed_use_existing_receipt_or_new_plan")
            value["task_confirmation"] = {"digest": args.digest, "confirmed": True}
            write_new(args.output, value)
        elif args.command == "confirm-server":
            value = load_plan(args.plan)
            if "task_confirmation" not in value:
                fail("tasks_must_be_confirmed_first")
            server = validate_server(read_json(args.server))
            if args.digest != server_digest(value, server):
                fail("server_digest_does_not_match_reviewed_proposal")
            if "server_confirmation" in value:
                fail("server_already_confirmed_use_tasks_receipt_for_new_server")
            value.update(server=server, server_confirmation={"digest": args.digest, "confirmed": True})
            write_new(args.output, value)
        else:
            export_setup(load_plan(args.plan), args.output_dir)
    except FileExistsError:
        parser.error("output_already_exists_choose_a_new_runtime_path")
    except SetupError as error:
        parser.error(str(error))
    except (OSError, UnicodeError):
        parser.error("cannot_read_or_write_configuration_check_local_paths_and_permissions")
    except (ValueError, KeyError, TypeError, AttributeError):
        # Input, model errors and filesystem paths can contain credentials; emit
        # bounded guidance rather than interpolating the underlying exception.
        parser.error("invalid_or_unconfirmed_configuration_review_inputs_and_confirmation_digests")
    print(json.dumps({"status": "written", "stage": args.command}))
    return 0


if __name__ == "__main__":
    # Redirected Windows stdout otherwise uses the system code page. The CLI
    # contract is UTF-8 JSON on every platform, including Chinese task names.
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
