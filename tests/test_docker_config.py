"""Container startup reads only explicit role secrets, without exposing them."""
import importlib.util
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


ENTRYPOINT = Path(__file__).resolve().parents[1] / "deploy" / "docker" / "entrypoint.py"


def module():
    spec = importlib.util.spec_from_file_location("docker_entrypoint", ENTRYPOINT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_file_secret_is_loaded_and_file_pointer_removed(tmp_path):
    secret = tmp_path / "read.token"
    secret.write_text("r" * 32 + "\n", encoding="utf-8")
    env = {"BRIEFING_READ_TOKEN_FILE": str(secret), "PATH": "unchanged"}
    result = module().load_role_secrets(env)
    assert result == {"BRIEFING_READ_TOKEN": "r" * 32, "PATH": "unchanged"}
    assert env == {"BRIEFING_READ_TOKEN_FILE": str(secret), "PATH": "unchanged"}


def test_model_key_is_left_to_provider_adapter(tmp_path):
    env = {"BRIEFING_LLM_API_KEY_FILE": str(tmp_path / "not-read-here")}
    assert module().load_role_secrets(env) == env


def test_trusted_proxy_configuration_accepts_only_exact_ipv4_and_ipv6():
    env = {"BRIEFING_TRUSTED_PROXY_IPS": "172.18.0.1, ::1, 172.18.0.1", "PATH": "unchanged"}
    assert module().configure_trusted_proxies(env) == {
        "FORWARDED_ALLOW_IPS": "172.18.0.1,::1", "PATH": "unchanged"}
    assert "FORWARDED_ALLOW_IPS" not in env


@pytest.mark.parametrize("value", ["*", "172.18.0.0/16", "proxy.local", "", "172.18.0.1,", "0.0.0.0", "::", "fe80::1%eth0"])
def test_trusted_proxy_configuration_rejects_broad_or_ambiguous_trust(value):
    with pytest.raises(ValueError, match="invalid_trusted_proxy_ips"):
        module().configure_trusted_proxies({"BRIEFING_TRUSTED_PROXY_IPS": value})


def test_direct_uvicorn_wildcard_is_also_rejected():
    with pytest.raises(ValueError, match="invalid_trusted_proxy_ips"):
        module().configure_trusted_proxies({"FORWARDED_ALLOW_IPS": "*"})


@pytest.mark.parametrize("client,expected_scheme", [("172.18.0.1", "https"), ("::1", "https"), ("172.18.0.2", "http")])
def test_uvicorn_honors_https_only_from_configured_exact_peer(client, expected_scheme):
    configured = module().configure_trusted_proxies({"BRIEFING_TRUSTED_PROXY_IPS": "172.18.0.1,::1"})
    observed = []
    async def application(scope, receive, send):
        observed.append(scope["scheme"])
    proxy = ProxyHeadersMiddleware(application, trusted_hosts=configured["FORWARDED_ALLOW_IPS"])
    scope = {"type": "http", "scheme": "http", "client": (client, 12345),
             "headers": [(b"host", b"briefing.example.com"), (b"x-forwarded-proto", b"https")]}
    asyncio.run(proxy(scope, None, None))
    assert observed == [expected_scheme]


@pytest.mark.parametrize("value", ["short", "a" * 24 + "\n" + "b" * 24, "a" * 4097, "a" * 24 + "\0"])
def test_invalid_role_secret_fails_without_value(tmp_path, value):
    secret = tmp_path / "read.token"
    secret.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        module().load_role_secrets({"BRIEFING_READ_TOKEN_FILE": str(secret)})
    assert str(failure.value) == "invalid_role_secret"


def test_ambiguous_direct_and_file_secret_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="ambiguous_role_secret"):
        module().load_role_secrets({"BRIEFING_READ_TOKEN": "r" * 32,
                                   "BRIEFING_READ_TOKEN_FILE": str(tmp_path / "missing")})


def test_missing_secret_file_returns_bounded_error(tmp_path):
    env = {**os.environ, "BRIEFING_READ_TOKEN_FILE": str(tmp_path / "secret-path.token")}
    result = subprocess.run([sys.executable, str(ENTRYPOINT), sys.executable, "-c", "raise SystemExit(99)"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "Container configuration error: unreadable_role_secret"
    assert "secret-path" not in result.stderr


def test_entrypoint_passes_tokens_only_in_child_environment(tmp_path, monkeypatch):
    secret = tmp_path / "read.token"
    secret.write_text("r" * 32, encoding="utf-8")
    loaded = module()
    monkeypatch.setattr(sys, "argv", [str(ENTRYPOINT), "python", "-c", "pass"])
    monkeypatch.setenv("BRIEFING_READ_TOKEN_FILE", str(secret))
    monkeypatch.delenv("BRIEFING_READ_TOKEN", raising=False)
    calls = []
    monkeypatch.setattr(loaded.os, "execvpe", lambda *args: calls.append(args))
    loaded.main()
    executable, arguments, environment = calls[0]
    assert executable == "python" and arguments == ["python", "-c", "pass"]
    assert environment["BRIEFING_READ_TOKEN"] == "r" * 32
    assert "BRIEFING_READ_TOKEN_FILE" not in environment


@pytest.mark.skipif(os.name == "nt", reason="Linux container process replacement; exercised by Linux CI")
def test_entrypoint_replaces_linux_process_with_child(tmp_path):
    secret = tmp_path / "read.token"
    secret.write_text("r" * 32, encoding="utf-8")
    env = {**os.environ, "BRIEFING_READ_TOKEN_FILE": str(secret)}
    env.pop("BRIEFING_READ_TOKEN", None)
    code = "import os; assert os.environ['BRIEFING_READ_TOKEN'] == 'r'*32; assert 'BRIEFING_READ_TOKEN_FILE' not in os.environ; print('child-ok')"
    result = subprocess.run([sys.executable, str(ENTRYPOINT), sys.executable, "-c", code],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "child-ok"
    assert result.stderr == ""
