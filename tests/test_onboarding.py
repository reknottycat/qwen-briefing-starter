"""First-install plans use synthetic local tasks and never change a scheduler."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from briefing import onboarding


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def task(task_id="daily-news", **changes):
    return {"id": task_id, "name": "我的每日新闻", "cadence": "daily", "enabled": True,
            "monitoring": False, "evidence": "每天提供完整新闻摘要，不以事件触发或无变化静默为目的。", **changes}


def selection(task_id="daily-news", **changes):
    return {"task_id": task_id, "category": "news", "mode": "processed",
            "narration_detail": "standard", **changes}


def plan(tmp_path, tasks=None, selections=None, work_enabled=False):
    inventory = write_json(tmp_path / "inventory.json", {"version": 1, "tasks": tasks or [task()]})
    choices = write_json(tmp_path / "selection.json", {"version": 1,
        "work_enabled": work_enabled, "sources": selections or [selection()]})
    output = tmp_path / "plan.json"
    assert onboarding.main(["plan", "--inventory", str(inventory), "--selection", str(choices),
                            "--output", str(output)]) == 0
    return output


def confirm_tasks(tmp_path, source):
    result = tmp_path / "tasks-confirmed.json"
    digest = onboarding.review(source)["task_digest"]
    assert onboarding.main(["confirm-tasks", "--plan", str(source), "--digest", digest,
                            "--confirm", "--output", str(result)]) == 0
    return result


def server():
    return {"deployment": "docker", "base_url": "https://briefing.example.com/briefing/v1",
            "ssh_alias": "briefing-host", "generator": "pi", "provider": "example-provider",
            "model": "example-model", "llm_base_url": "https://api.example.com/v1"}


def confirm_server(tmp_path, source):
    config = write_json(tmp_path / "server.json", server())
    output = tmp_path / "server-confirmed.json"
    digest = onboarding.review(source, config)["server_digest"]
    assert onboarding.main(["confirm-server", "--plan", str(source), "--server", str(config),
                            "--digest", digest, "--confirm", "--output", str(output)]) == 0
    return output


def test_full_flow_exports_confirmed_custom_names_without_scheduler_edits(tmp_path):
    source = plan(tmp_path, [task(), task("learn", name="每日科学小课")],
                  [selection(), selection("learn", category="learning", narration_detail="detailed")])
    input_bytes = (tmp_path / "inventory.json").read_bytes()
    confirmed = confirm_server(tmp_path, confirm_tasks(tmp_path, source))
    output = tmp_path / "setup"
    assert onboarding.main(["export", "--plan", str(confirmed), "--output-dir", str(output)]) == 0
    policy = json.loads((output / "source-policy.json").read_text(encoding="utf-8"))
    assert [x["name"] for x in policy["sources"]] == ["我的每日新闻", "每日科学小课"]
    assert all(x["kind"] == "daily" and x["event_type"] == "定时结果" for x in policy["sources"])
    prefs = json.loads((output / "initial-preferences.json").read_text(encoding="utf-8"))
    assert prefs["sources"]["每日科学小课"]["category"] == "learning"
    assert prefs["work_enabled"] is False
    assert prefs["revision"] == 1
    assert "[每日早报][每日科学小课][YYYY-MM-DD]" in (output / "mail-delivery.md").read_text(encoding="utf-8")
    manifest = json.loads((output / "setup-manifest.json").read_text(encoding="utf-8"))
    assert manifest["qwen_handoff"]["ready_for_configuration"] is True
    assert manifest["qwen_handoff"]["device_verified"] is False
    assert (tmp_path / "inventory.json").read_bytes() == input_bytes


@pytest.mark.parametrize("changes", [{"cadence": "weekly"}, {"monitoring": True},
                                    {"enabled": False}, {"cadence": "unknown"},
                                    {"monitoring": None, "evidence": "需人工检查"}])
def test_ineligible_or_unreviewed_tasks_cannot_be_selected(tmp_path, changes):
    with pytest.raises(SystemExit) as result:
        plan(tmp_path, [task(**changes)])
    assert result.value.code == 2
    assert not (tmp_path / "plan.json").exists()


def test_unknown_monitoring_requires_explicit_evidence_backed_review(tmp_path):
    source = plan(tmp_path, [task(monitoring=None, evidence="待核对原任务")],
                  [selection(monitoring_review={"monitoring": False, "evidence": "已核对任务正文，每日生成课程。"})])
    assert onboarding.review(source)["tasks_confirmed"] is False


def test_known_monitor_cannot_be_overridden_by_selection(tmp_path):
    with pytest.raises(SystemExit):
        plan(tmp_path, [task(monitoring=True)],
             [selection(monitoring_review={"monitoring": False, "evidence": "试图覆盖监控属性"})])


def test_excluded_tasks_are_reported_and_not_exported(tmp_path):
    source = plan(tmp_path, [task(), task("monitor", name="股价提醒", monitoring=True),
                            task("weekly", name="每周论文", cadence="weekly")])
    value = json.loads(source.read_text(encoding="utf-8"))
    assert {x["task_id"] for x in value["excluded"]} == {"monitor", "weekly"}


def test_task_confirmation_rejects_stale_digest(tmp_path):
    source = plan(tmp_path)
    digest = onboarding.review(source)["task_digest"]
    value = json.loads(source.read_text(encoding="utf-8"))
    value["tasks"][0]["category"] = "learning"
    write_json(source, value)
    with pytest.raises(SystemExit):
        onboarding.main(["confirm-tasks", "--plan", str(source), "--digest", digest,
                         "--confirm", "--output", str(tmp_path / "confirmed.json")])
    assert not (tmp_path / "confirmed.json").exists()


def test_task_edit_invalidates_both_confirmations(tmp_path):
    confirmed = confirm_server(tmp_path, confirm_tasks(tmp_path, plan(tmp_path)))
    value = json.loads(confirmed.read_text(encoding="utf-8"))
    value["tasks"][0]["category"] = "learning"
    write_json(confirmed, value)
    with pytest.raises(SystemExit):
        onboarding.main(["export", "--plan", str(confirmed), "--output-dir", str(tmp_path / "setup")])
    assert not (tmp_path / "setup").exists()


def test_server_edit_invalidates_confirmation(tmp_path):
    confirmed = confirm_server(tmp_path, confirm_tasks(tmp_path, plan(tmp_path)))
    value = json.loads(confirmed.read_text(encoding="utf-8"))
    value["server"]["base_url"] = "https://changed.example.com/briefing/v1"
    write_json(confirmed, value)
    with pytest.raises(SystemExit):
        onboarding.main(["export", "--plan", str(confirmed), "--output-dir", str(tmp_path / "setup")])


def test_server_confirmation_requires_task_confirmation(tmp_path):
    source = plan(tmp_path)
    config = write_json(tmp_path / "server.json", server())
    with pytest.raises(SystemExit):
        onboarding.main(["confirm-server", "--plan", str(source), "--server", str(config),
                         "--digest", "0" * 64, "--confirm", "--output", str(tmp_path / "confirmed.json")])


@pytest.mark.parametrize("stage", ["plan", "tasks"])
def test_export_requires_both_confirmations(tmp_path, stage):
    source = plan(tmp_path)
    if stage == "tasks":
        source = confirm_tasks(tmp_path, source)
    with pytest.raises(SystemExit):
        onboarding.main(["export", "--plan", str(source), "--output-dir", str(tmp_path / "setup")])
    assert not (tmp_path / "setup").exists()


@pytest.mark.parametrize("changes", [{"password": "do-not-print-this"},
    {"api_key": "do-not-print-this"}, {"base_url": "https://user:do-not-print-this@example.com"},
    {"llm_base_url": "https://api.example.com/v1?key=do-not-print-this"},
    {"ssh_alias": "user@example.com -oProxyCommand=bad"}, {"deployment": "kubernetes"}])
def test_server_rejects_secrets_or_unsafe_destinations_without_echo(tmp_path, capsys, changes):
    source = confirm_tasks(tmp_path, plan(tmp_path))
    config = write_json(tmp_path / "server.json", {**server(), **changes})
    with pytest.raises(SystemExit):
        onboarding.main(["status", "--plan", str(source), "--server", str(config)])
    assert "do-not-print-this" not in capsys.readouterr().err


def test_existing_files_and_export_directory_are_never_overwritten(tmp_path):
    source = plan(tmp_path)
    before = source.read_bytes()
    with pytest.raises(SystemExit):
        onboarding.main(["confirm-tasks", "--plan", str(source), "--digest", onboarding.review(source)["task_digest"],
                         "--confirm", "--output", str(source)])
    assert source.read_bytes() == before
    final = confirm_server(tmp_path, confirm_tasks(tmp_path, source))
    output = tmp_path / "setup"
    output.mkdir()
    (output / "keep").write_text("preserve")
    with pytest.raises(SystemExit):
        onboarding.main(["export", "--plan", str(final), "--output-dir", str(output)])
    assert list(output.iterdir()) == [output / "keep"]


def test_codex_discovery_is_read_only_conservative_and_does_not_copy_prompts(tmp_path):
    root = tmp_path / "automations"
    daily = root / "daily"
    daily.mkdir(parents=True)
    raw = 'id = "daily"\nname = "每日课程"\nstatus = "ACTIVE"\nrrule = "FREQ=DAILY;BYHOUR=8;BYMINUTE=0"\nprompt = "synthetic private prompt"\n'
    (daily / "automation.toml").write_text(raw, encoding="utf-8")
    weekly = root / "weekly"
    weekly.mkdir()
    (weekly / "automation.toml").write_text(raw.replace('id = "daily"', 'id = "weekly"').replace('FREQ=DAILY', 'FREQ=WEEKLY'), encoding="utf-8")
    output = tmp_path / "discovered.json"
    assert onboarding.main(["discover", "--codex-dir", str(root), "--output", str(output)]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert {item["cadence"] for item in data["tasks"]} == {"daily", "weekly"}
    assert all(item["monitoring"] is None for item in data["tasks"])
    assert "synthetic private prompt" not in output.read_text(encoding="utf-8")
    assert (daily / "automation.toml").read_text(encoding="utf-8") == raw


def test_work_inclusion_does_not_implicitly_enable_work_view(tmp_path):
    source = plan(tmp_path, selections=[selection(category="work")])
    assert json.loads(source.read_text(encoding="utf-8"))["work_enabled"] is False


def test_duplicate_source_names_and_invalid_categories_fail_before_write(tmp_path):
    with pytest.raises(SystemExit):
        plan(tmp_path, [task(), task("other")], [selection(), selection("other")])
    assert not (tmp_path / "plan.json").exists()


def test_confirmation_flag_is_required(tmp_path):
    source = plan(tmp_path)
    with pytest.raises(SystemExit):
        onboarding.main(["confirm-tasks", "--plan", str(source), "--digest", onboarding.review(source)["task_digest"],
                         "--output", str(tmp_path / "confirmed.json")])


@pytest.mark.parametrize("generator,deployment", [("codex", "linux"), ("pi", "linux"), ("pi", "docker")])
def test_supported_server_generators_are_independent_of_agent_frontend(generator, deployment):
    config = {**server(), "generator": generator, "deployment": deployment}
    assert onboarding.validate_server(config)["generator"] == generator


def test_codex_docker_is_rejected_with_supported_deployment_guidance():
    with pytest.raises(onboarding.SetupError, match="codex_requires_linux_choose_pi_for_docker"):
        onboarding.validate_server({**server(), "generator": "codex", "deployment": "docker"})


def test_pi_requires_explicit_provider_and_model():
    config = {**server()}
    del config["model"]
    with pytest.raises(onboarding.SetupError, match="pi_provider_and_model_required"):
        onboarding.validate_server(config)


@pytest.mark.parametrize("generator", ["opencode", "openai-compatible"])
def test_agent_frontends_are_not_invented_server_generators(generator):
    with pytest.raises(onboarding.SetupError, match="invalid_server_choice"):
        onboarding.validate_server({**server(), "generator": generator})


def test_unknown_credential_arguments_are_not_echoed(capsys):
    with pytest.raises(SystemExit):
        onboarding.main(["discover", "--tasks", "input.json", "--output", "output.json",
                         "--api-key", "do-not-echo-synthetic-secret"])
    assert "do-not-echo-synthetic-secret" not in capsys.readouterr().err


def test_cli_processes_complete_resumable_first_install(tmp_path):
    root = Path(__file__).resolve().parents[1]
    def run(*args):
        result = subprocess.run([sys.executable, "-m", "briefing.onboarding", *map(str, args)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    inventory = tmp_path / "inventory.json"
    run("discover", "--tasks", root / "examples/tasks.example.json", "--output", inventory)
    choices = write_json(tmp_path / "selection.json", {"version": 1, "sources": [selection("daily-news-example")]})
    proposal = tmp_path / "plan.json"
    run("plan", "--inventory", inventory, "--selection", choices, "--output", proposal)
    tasks_receipt = tmp_path / "confirmed-tasks.json"
    task_digest = run("status", "--plan", proposal)["task_digest"]
    run("confirm-tasks", "--plan", proposal, "--digest", task_digest, "--confirm", "--output", tasks_receipt)
    config = write_json(tmp_path / "server.json", server())
    server_digest = run("status", "--plan", tasks_receipt, "--server", config)["server_digest"]
    server_receipt = tmp_path / "confirmed-server.json"
    run("confirm-server", "--plan", tasks_receipt, "--server", config, "--digest", server_digest,
        "--confirm", "--output", server_receipt)
    run("export", "--plan", server_receipt, "--output-dir", tmp_path / "setup")
    assert (tmp_path / "setup" / "initial-preferences.json").exists()
    assert run("status", "--plan", server_receipt)["server_confirmed"] is True


@pytest.mark.parametrize("rule", ["FREQ=DAILY;BYHOUR=99", "FREQ=DAILY;BYMINUTE=60",
    "FREQ=DAILY;BYYEARDAY=1", "FREQ=DAILY;BYWEEKNO=1", "FREQ=DAILY;UNKNOWN=1",
    "FREQ=DAILY;BYHOUR=", "FREQ=DAILY;BYSECOND=-1", "FREQ=DAILY;COUNT=1"])
def test_codex_discovery_does_not_assume_unknown_or_invalid_rules_are_daily(rule):
    assert onboarding.cadence_from_rrule(rule) != "daily"


@pytest.mark.parametrize("rule", ["FREQ=DAILY;BYHOUR=8;BYMINUTE=00", "RRULE:FREQ=DAILY;BYHOUR=8,20",
                                  "FREQ=DAILY;INTERVAL=1;BYSECOND=0;WKST=MO"])
def test_known_daily_rules_are_recognized(rule):
    assert onboarding.cadence_from_rrule(rule) == "daily"
