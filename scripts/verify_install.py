"""Check the installed distribution, packaged UI, and local authenticated API."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

import briefing
from fastapi.testclient import TestClient

from briefing.api import PREFIX, create_app


def main() -> None:
    checkout = Path(__file__).resolve().parents[1]
    module = Path(briefing.__file__).resolve()
    if module.is_relative_to(checkout / "briefing"):
        raise RuntimeError("Run with python -I after installing the wheel, not an editable checkout")
    version = importlib.metadata.version("qwen-briefing-starter")
    tokens = {role: secrets.token_urlsafe(32) for role in ("read", "ingest", "worker", "manage")}
    checks = []
    setup = subprocess.run([sys.executable, "-I", "-m", "briefing.onboarding", "--help"],
                           capture_output=True, text=True, timeout=10)
    assert setup.returncode == 0 and "confirm-tasks" in setup.stdout and "confirm-server" in setup.stdout
    checks.append("installed_setup_cli_available")
    catalog = json.loads((module.parent / "pi_model_catalog.json").read_text(encoding="utf-8"))
    provider_ids = {item["id"] for item in catalog["providers"]}
    assert catalog["version"] == "1.4.0"
    assert {"deepseek", "minimax", "minimax-cn", "moonshotai", "kimi-coding"} <= provider_ids
    assert all(item["models"] for item in catalog["providers"])
    checks.append("packaged_pi_model_catalog_loaded")
    with tempfile.TemporaryDirectory(prefix="briefing-install-") as directory:
        with TestClient(create_app(Path(directory), tokens), base_url="https://testserver") as client:
            assert client.get(PREFIX + "/health").json()["status"] == "ok"
            checks.append("installed_api_health")
            assert client.get(PREFIX + "/read").status_code == 401
            assert client.get(PREFIX + "/manage/preferences").status_code == 401
            checks.append("unauthenticated_reads_rejected")
            prefs = client.get(PREFIX + "/manage/preferences", headers={"Authorization": "Bearer " + tokens["manage"]})
            assert prefs.status_code == 200 and prefs.json()["work_enabled"] is False
            assert prefs.json()["sources"]
            checks.append("packaged_source_policy_loaded")
            work = client.get(PREFIX + "/read", params={"view": "work"}, headers={"Authorization": "Bearer " + tokens["read"]})
            assert work.status_code == 200 and work.json()["status"] == "disabled"
            checks.append("work_disabled_by_default")
            for asset in ("", "app.js", "styles.css"):
                response = client.get(PREFIX + "/manage/" + asset)
                assert response.status_code == 200 and len(response.content) > 100
            checks.append("packaged_management_assets_served")
    print(json.dumps({"status": "passed", "version": version, "checks": checks}))


if __name__ == "__main__":
    main()
