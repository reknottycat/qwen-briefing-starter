"""Build and exercise an isolated Docker stack using a synthetic HTTPS model.

Requires Docker Compose and openssl. No real mail, cloud account, or paid model
is used. Only the newly created Compose project is stopped and removed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


class SmokeFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise SmokeFailure(code)


def write_json(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def run(command, cwd, env, log, timeout=300):
    with log.open("ab") as output:
        try:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=output, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SmokeFailure("command_unavailable_or_timed_out") from None
    require(result.returncode == 0, "command_failed")


def request(base, path, *, token=None, payload=None, extra_headers=None, response_headers=None):
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    started = time.monotonic()
    try:
        response = build_opener(ProxyHandler({})).open(Request(base + path, data=data, headers=headers), timeout=3)
    except HTTPError as error:
        response = error
    with response:
        body = json.loads(response.read(1_000_000))
        status = response.status
        if response_headers is not None:
            response_headers.update(response.headers.items())
    require(time.monotonic() - started < 3, "http_deadline_exceeded")
    return status, body


def wait_for(call, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = call()
            if result:
                return result
        except (URLError, OSError, ValueError):
            pass
        time.sleep(1)
    raise SmokeFailure("readiness_timeout")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runtime/docker-smoke/report.json"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    project = "briefing-smoke-" + secrets.token_hex(5)
    directory = args.output.parent / project
    directory.mkdir(mode=0o700)
    report = {"scope": "docker_synthetic_model", "status": "failed", "checks": [], "project": project}
    stage = "prerequisites"
    compose = None
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BRIEFING_", "PI_", "COMPOSE_"))}
    try:
        require(shutil.which("docker") and shutil.which("openssl"), "docker_and_openssl_required")
        run(["docker", "info", "--format", "{{.ServerVersion}}"], root, env, directory / "commands.log", 30)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        for name in ("config", "credentials", "data", "worker", "fixture", "receipt"):
            (directory / name).mkdir(mode=0o700)
        tokens = {role: secrets.token_urlsafe(32) for role in ("read", "worker", "ingest", "manage")}
        for role, value in tokens.items():
            (directory / "credentials" / (role + ".token")).write_text(value, encoding="utf-8")
        key = "synthetic-provider-key-" + secrets.token_hex(12)
        (directory / "credentials" / "llm-api-key").write_text(key, encoding="utf-8")
        (directory / "fixture" / "key").write_text(key, encoding="utf-8")
        names = ("容器合成资讯", "容器合成学习")
        write_json(directory / "config" / "source-policy.json", {"version": 1, "sources": [
            {"name": name, "kind": "daily", "event_type": "定时结果", "narration_detail": "standard"} for name in names]})
        write_json(directory / "config" / "initial-preferences.json", {
            "revision": 1, "format": "mixed", "work_enabled": False, "sources": {
                name: {"mode": "processed", "category": category, "narration_detail": "standard", "note": ""}
                for name, category in zip(names, ("news", "learning"))}})
        stage = "tls_fixture"
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-keyout", str(directory / "fixture" / "model.key"), "-out", str(directory / "fixture" / "model.crt"),
             "-subj", "/CN=mock-llm", "-addext", "subjectAltName=DNS:mock-llm"], root, env, directory / "commands.log", 30)
        image = project + ":local"
        env.update({"BRIEFING_IMAGE": image, "BRIEFING_PORT": str(port),
                    "BRIEFING_UID": str(os.getuid()) if hasattr(os, "getuid") else "1000",
                    "BRIEFING_GID": str(os.getgid()) if hasattr(os, "getgid") else "1000",
                    "PI_PROVIDER": "smoke-provider", "PI_MODEL": "smoke-model", "PI_BASE_URL": "https://mock-llm:8443/v1"})
        env.update({variable: (directory / child).as_posix() for variable, child in (
            ("BRIEFING_CONFIG_DIR", "config"), ("BRIEFING_CREDENTIALS_DIR", "credentials"),
            ("BRIEFING_DATA_DIR", "data"), ("BRIEFING_WORKER_DIR", "worker"))})
        for secret in (directory / "credentials").iterdir():
            secret.chmod(0o600)
        override = {"services": {
            "api": {"restart": "no"},
            "worker": {"restart": "no", "command": ["python", "-m", "briefing.worker", "--interval", "10", "--timeout", "60"],
                       "environment": {"PI_CA_FILE": "/run/briefing/model.crt"},
                       "volumes": [(directory / "fixture" / "model.crt").as_posix() + ":/run/briefing/model.crt:ro"],
                       "depends_on": {"mock-llm": {"condition": "service_healthy"}}},
            "mock-llm": {"image": image, "user": env["BRIEFING_UID"] + ":" + env["BRIEFING_GID"],
                         "entrypoint": ["python", "/mock/server.py"],
                         "volumes": [(root / "deploy/docker/mock_llm.py").as_posix() + ":/mock/server.py:ro",
                                     (directory / "fixture").as_posix() + ":/fixture:ro",
                                     (directory / "receipt").as_posix() + ":/receipt"],
                         "healthcheck": {"test": ["CMD", "python", "-c", "import socket; socket.create_connection(('127.0.0.1',8443),2).close()"],
                                         "interval": "2s", "timeout": "3s", "retries": 15}}}}
        write_json(directory / "override.json", override)
        (directory / "empty.env").write_text("", encoding="utf-8")
        compose = ["docker", "compose", "--project-name", project, "--project-directory", str(root),
                   "--env-file", str(directory / "empty.env"), "-f", str(root / "compose.yaml"),
                   "-f", str(directory / "override.json")]
        stage = "build_and_start"
        run(compose + ["up", "--detach", "--build", "--wait", "--wait-timeout", "90"], root, env, directory / "commands.log", 600)
        report["checks"].append("image_built_and_services_healthy")
        base = f"http://127.0.0.1:{port}/briefing/v1"
        stage = "authentication_and_ingestion"
        require(request(base, "/health")[1]["status"] == "ok", "health_failed")
        require(request(base, "/read")[0] == 401, "anonymous_read_allowed")
        stage = "management_behind_exact_trusted_proxy"
        proxy_headers = {"Host": "briefing-smoke.example", "Origin": "https://briefing-smoke.example",
                         "X-Forwarded-Proto": "https"}
        require(request(base, "/manage/session", payload={"token": tokens["manage"]}, extra_headers=proxy_headers)[0] == 403,
                "untrusted_peer_can_forge_https")
        gateway_result = subprocess.run(["docker", "network", "inspect", project + "_default",
                                        "--format", "{{json .IPAM.Config}}"], cwd=root, env=env,
                                       capture_output=True, text=True, timeout=15)
        require(gateway_result.returncode == 0, "docker_gateway_inspection_failed")
        gateways = [item["Gateway"] for item in json.loads(gateway_result.stdout) if item.get("Gateway")]
        gateway = next((value for value in gateways if ipaddress.ip_address(value).version == 4), None)
        require(gateway is not None, "docker_ipv4_gateway_missing")
        env["BRIEFING_TRUSTED_PROXY_IPS"] = gateway
        run(compose + ["up", "--detach", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "api"],
            root, env, directory / "commands.log", 90)
        session_headers = {}
        status, session = request(base, "/manage/session", payload={"token": tokens["manage"]},
                                  extra_headers=proxy_headers, response_headers=session_headers)
        require(status == 200 and session.get("status") == "authenticated", "trusted_proxy_login_failed")
        cookie = SimpleCookie(session_headers.get("set-cookie", session_headers.get("Set-Cookie", "")))
        require("briefing_manage_session" in cookie and cookie["briefing_manage_session"]["secure"], "session_cookie_not_secure")
        authorized_headers = {**proxy_headers, "Cookie": "briefing_manage_session=" + cookie["briefing_manage_session"].value}
        status, settings = request(base, "/manage/preferences", extra_headers=authorized_headers)
        require(status == 200 and any(item.get("name") == names[1] and item.get("category") == "learning"
                                     for item in settings.get("sources", [])),
                "trusted_proxy_preferences_failed")
        require(request(base, "/manage/preferences", extra_headers={**authorized_headers, "Origin": "https://wrong.example"})[0] == 403,
                "trusted_proxy_bypassed_origin_check")
        report["checks"].append("management_https_proxy_exact_peer_and_origin")
        stage = "authentication_and_ingestion"
        now = datetime.now(timezone.utc)
        day = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
        messages = [{"id": sid, "subject": f"[每日早报][{name}][{day}]", "received_at": now.isoformat(),
                     "body": "早报事件类型：定时结果\n合成输入：" + body}
                    for sid, name, body in zip(("smoke-news", "smoke-study"), names,
                        ("项目进展尚未核实。", "水循环包括蒸发、凝结和降水。练习说明这三个阶段。"))]
        require(request(base, "/ingest", token=tokens["read"], payload={"messages": messages})[0] == 401, "read_role_can_write")
        status, ingested = request(base, "/ingest", token=tokens["ingest"], payload={"messages": messages})
        require(status == 200 and ingested.get("accepted") == 2, "custom_source_ingestion_failed")
        report["checks"].append("custom_sources_and_scoped_auth")
        stage = "pi_generation_and_cached_read"
        def ready():
            status, content = request(base, "/read?view=full&date=" + day, token=tokens["read"])
            return content if status == 200 and content.get("status") == "ready" else None
        content = wait_for(ready, 90)
        require(content["total_sections"] == 2 and all(part in content["full_spoken_text"] for part in
                ("合成资讯已整理", "合成学习正文", "本次早报内容已播完")), "cached_full_content_mismatch")
        model = json.loads((directory / "receipt" / "request.json").read_text())
        require(model["requests"] >= 1 and model["tools"] == 0, "model_protocol_not_observed")
        report["checks"].append("real_pi_cli_with_synthetic_https_provider")
        daily = request(base, "/read?view=daily&date=" + day, token=tokens["read"])[1]
        require("今天学习水循环" in daily["full_spoken_text"] and "合成学习正文" not in daily["full_spoken_text"], "learning_overview_mismatch")
        require(request(base, "/read?view=work", token=tokens["read"])[1]["status"] == "disabled", "work_default_not_disabled")
        report["checks"].append("daily_learning_summary_and_work_opt_in")
        stage = "persistence"
        run(compose + ["restart", "api"], root, env, directory / "commands.log", 60)
        persisted = wait_for(ready, 45)
        require(persisted["full_spoken_text"] == content["full_spoken_text"], "restart_lost_cached_content")
        report["checks"].append("cached_content_survives_api_restart")
        report["status"] = "passed"
    except (SmokeFailure, KeyError, OSError, ValueError) as error:
        report["stage"] = stage
        report["reason"] = str(error) if isinstance(error, SmokeFailure) else "unexpected_contract_or_environment_error"
    finally:
        if compose:
            try:
                run(compose + ["logs", "--no-color", "--tail", "120"], root, env, directory / "services.log", 30)
            except SmokeFailure:
                report["diagnostics"] = "service_logs_unavailable"
            try:
                run(compose + ["down", "--volumes", "--remove-orphans"], root, env, directory / "commands.log", 60)
                image_exists = subprocess.run(["docker", "image", "inspect", project + ":local"], cwd=root, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                if image_exists.returncode == 0:
                    run(["docker", "image", "rm", project + ":local"], root, env, directory / "commands.log", 30)
            except SmokeFailure:
                report["status"] = "failed"
                report["cleanup"] = "inspect_isolated_project"
            except (OSError, subprocess.TimeoutExpired):
                report["status"] = "failed"
                report["cleanup"] = "inspect_isolated_project"
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
