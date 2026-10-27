"""Pi transport checks use synthetic subprocesses, not real model accounts."""
import json
from pathlib import Path
import sys

import pytest

from briefing import generators
from briefing.worker import GenerationFailure, run_generator, valid_backend_url
from test_worker import source, draft


def events(value=None, reason="stop", settled=True):
    items = [{"type": "message_update", "delta": "must not use stream delta"},
             {"type": "message_end", "message": {"role": "assistant", "stopReason": reason,
              "provider": "synthetic", "model": "synthetic-model",
              "content": [{"type": "text", "text": json.dumps(value or draft(), ensure_ascii=False)}]}}]
    if settled:
        items.append({"type": "agent_settled", "aborted": False})
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)


@pytest.fixture
def pi(monkeypatch, tmp_path):
    key = tmp_path / "provider.key"
    key.write_text("synthetic-provider-key", encoding="utf-8")
    monkeypatch.setenv("PI_PROVIDER", "synthetic")
    monkeypatch.setenv("PI_MODEL", "synthetic-model")
    monkeypatch.setenv("PI_BASE_URL", "https://model.example.test/v1")
    monkeypatch.setenv("PI_API_KEY_FILE", str(key))
    monkeypatch.setenv("BRIEFING_GENERATOR", "pi")
    return key


def fake_pi(monkeypatch, tmp_path, code):
    executable = tmp_path / "synthetic-cli.py"
    executable.write_text(code, encoding="utf-8")
    monkeypatch.setattr(generators, "pi_command", lambda binary, provider, model, directory: [sys.executable, str(executable)])


def test_pi_command_is_noninteractive_tool_free_and_never_exposes_prompt_or_secret(tmp_path):
    command = generators.pi_command("pi", "synthetic", "model", tmp_path)
    for flag in ("--print", "--no-session", "--no-tools", "--no-extensions", "--no-mcp", "--no-skills",
                 "--no-prompt-templates", "--no-context-files", "--no-themes", "--no-approve", "--offline"):
        assert flag in command
    assert "--api-key" not in command
    assert command[command.index("--mode") + 1] == "json"


@pytest.mark.parametrize("provider,model,selector", [
    ("openrouter", "openrouter/auto", "openrouter/openrouter/auto"),
    ("openai", "openai/gpt-5.5", "openai/openai/gpt-5.5"),
    ("synthetic", "synthetic-model", "synthetic/synthetic-model"),
])
def test_pi_model_selector_does_not_confuse_provider_prefix_with_exact_model_id(tmp_path, provider, model, selector):
    # Pi resolves a provider prefix before exact IDs, even with --provider present.
    # Prefix the selector explicitly so an ID which itself contains provider/ survives.
    command = generators.pi_command("pi", provider, model, tmp_path)
    assert command[command.index("--provider") + 1] == provider
    assert command[command.index("--model") + 1] == selector


def test_real_subprocess_receives_finite_stdin_isolated_config_and_returns_valid_draft(pi, monkeypatch, tmp_path):
    monkeypatch.setenv("BRIEFING_WORKER_TOKEN", "private-role-token")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-account-key")
    output = repr(events())
    fake_pi(monkeypatch, tmp_path, f'''import json, os, pathlib, sys
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
prompt = sys.stdin.read()
assert "$(echo test)" in prompt
assert "BRIEFING_WORKER_TOKEN" not in os.environ
assert "OPENAI_API_KEY" not in os.environ
agent = pathlib.Path(os.environ["PI_CODING_AGENT_DIR"])
assert json.loads((agent / "auth.json").read_text())["synthetic"]["key"] == "synthetic-provider-key"
assert json.loads((agent / "models.json").read_text())["providers"]["synthetic"]["api"] == "openai-completions"
assert json.loads((agent / "settings.json").read_text())["retry"]["enabled"] is False
assert pathlib.Path(os.environ["HOME"]).is_relative_to(pathlib.Path.cwd())
print({output})
''')
    runtime = tmp_path / "worker"
    assert run_generator(source(), "unused", runtime) == draft()
    assert list(runtime.iterdir()) == []


def test_raw_mode_never_invokes_pi(pi, monkeypatch, tmp_path):
    monkeypatch.setattr(generators.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not call model"))
    raw = source("raw")
    assert run_generator(raw, "unused", tmp_path)["sections"][0]["text"] == raw["messages"][0]["body"].split("\n", 1)[1]


@pytest.mark.parametrize("reason", ["length", "toolUse", "error", "aborted", "deferred"])
def test_pi_rejects_incomplete_or_tool_completion(reason):
    with pytest.raises(generators.AdapterFailure, match="invalid_output"):
        generators.parse_pi_output(events(reason=reason), 32)


@pytest.mark.parametrize("output", [events(settled=False), events() + '\n{"type":"error"}',
    events() + '\n{"type":"tool_execution_start"}', events() + '\n{"type":"agent_settled","aborted":true}',
    events() + '\n{"type":"auto_retry_end","success":false}', "not-json"])
def test_pi_requires_settled_success_and_rejects_failure_events(output):
    with pytest.raises(generators.AdapterFailure, match="invalid_output"):
        generators.parse_pi_output(output, 32)


def test_pi_jsonl_uses_lf_not_unicode_line_separator():
    result = draft()
    result["sections"][0]["text"] = "前半段\u2028后半段"
    assert generators.parse_pi_output(events(result), 32) == result


def test_pi_rejects_fuzzy_model_selection_or_wrong_provider():
    with pytest.raises(generators.AdapterFailure, match="invalid_output"):
        generators.parse_pi_output(events(), 32, provider="other", model="synthetic-model")
    with pytest.raises(generators.AdapterFailure, match="invalid_output"):
        generators.parse_pi_output(events(), 32, provider="synthetic", model="other")


def test_pi_failure_hides_stderr_and_credentials(pi, monkeypatch, tmp_path, capsys):
    fake_pi(monkeypatch, tmp_path, "import sys; print('private provider body', file=sys.stderr); raise SystemExit(1)")
    with pytest.raises(GenerationFailure, match="^generation_failed$"):
        run_generator(source(), "unused", tmp_path / "worker")
    assert "private" not in str(capsys.readouterr())


def test_pi_timeout_kills_process_and_removes_private_runtime(pi, monkeypatch, tmp_path):
    fake_pi(monkeypatch, tmp_path, "import time; time.sleep(60)")
    runtime = tmp_path / "worker"
    with pytest.raises(GenerationFailure, match="timeout"):
        run_generator(source(), "unused", runtime, timeout=0.1)
    assert list(runtime.iterdir()) == []


def test_pi_rejects_oversized_events_without_reading_them(pi, monkeypatch, tmp_path):
    fake_pi(monkeypatch, tmp_path, f"print('x' * {generators.MAX_CLI_OUTPUT_BYTES + 1})")
    with pytest.raises(GenerationFailure, match="invalid_output"):
        run_generator(source(), "unused", tmp_path / "worker")


@pytest.mark.parametrize("key", ["!echo bad", "two lines\nkey", ""])
def test_pi_rejects_executable_or_invalid_secret_resolvers(pi, monkeypatch, tmp_path, key):
    pi.write_text(key, encoding="utf-8")
    monkeypatch.setattr(generators.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start CLI"))
    with pytest.raises(GenerationFailure, match="generation_failed"):
        run_generator(source(), "unused", tmp_path)


@pytest.mark.parametrize("literal,encoded", [
    ("synthetic$UNSET_KEY_SUFFIX", "synthetic$$UNSET_KEY_SUFFIX"),
    ("synthetic${UNSET_KEY_SUFFIX}", "synthetic$${UNSET_KEY_SUFFIX}"),
    ("synthetic$$suffix", "synthetic$$$$suffix"),
    ("synthetic$!suffix", "synthetic$$!suffix"),
])
def test_pi_credentials_preserve_literal_dollars_in_generated_auth(pi, monkeypatch, tmp_path, literal, encoded):
    pi.write_text(literal, encoding="utf-8")
    def execute(command, directory, environment, timeout):
        # Pi 1.4.0 resolves $NAME, ${NAME}, $$ and $! inside auth values.
        # The generated private config must encode a literal, not an env reference.
        auth = json.loads((Path(environment["PI_CODING_AGENT_DIR"]) / "auth.json").read_text())
        assert auth["synthetic"]["key"] == encoded
        return events()
    monkeypatch.setattr(generators, "_execute", execute)
    assert run_generator(source(), "unused", tmp_path / "worker") == draft()
    assert pi.read_text(encoding="utf-8") == literal


@pytest.mark.parametrize("url", ["http://model.example.test/v1", "https://a:b@model.example.test", "https://model.example.test?key=x", "https://model.example.test/#x", "https://model.example.test:99999/v1"])
def test_pi_rejects_invalid_custom_endpoints_before_starting(pi, monkeypatch, tmp_path, url):
    monkeypatch.setenv("PI_BASE_URL", url)
    monkeypatch.setattr(generators.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start CLI"))
    with pytest.raises(GenerationFailure, match="generation_failed"):
        run_generator(source(), "unused", tmp_path)


@pytest.mark.parametrize("url,flag,expected", [
    ("https://briefing.example.test/briefing/v1", False, True),
    ("http://api:8787/briefing/v1", True, True),
    ("http://127.0.0.1:8787/briefing/v1", True, True),
    ("http://[::1]:8787/briefing/v1", True, True),
    ("http://api:8787/briefing/v1", False, False),
    ("http://public.example.test/briefing/v1", True, False),
    ("http://10.0.0.1:8787/briefing/v1", True, False),
    ("https://a:b@example.test/briefing/v1", False, False),
    ("https://example.test:99999/briefing/v1", False, False),
])
def test_internal_api_transport_requires_flag_and_exact_local_target(url, flag, expected):
    assert valid_backend_url(url, allow_internal=flag) is expected


def test_unknown_generator_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIEFING_GENERATOR", "typo")
    with pytest.raises(GenerationFailure, match="generation_failed"):
        run_generator(source(), "unused", tmp_path)
