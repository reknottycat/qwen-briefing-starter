"""Private model settings: authorization, atomic persistence and worker snapshots."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat

from fastapi.testclient import TestClient
import httpx
import pytest

from briefing.api import create_app
from briefing.worker import run_generator, run_once
from test_worker import draft, source
from test_generators import events

MANAGE = "/briefing/v1/manage"
TOKENS = {role: role + "-" + "s" * 32 for role in ("read", "worker", "ingest", "manage")}
KEY = "synthetic-key-not-for-real-service"


def headers(role="manage"):
    return {"Authorization": "Bearer " + TOKENS[role]}


def builtin(revision=0, key=KEY):
    catalog = json.loads((Path(__file__).resolve().parents[1] / "briefing/pi_model_catalog.json").read_text(encoding="utf-8"))
    provider = next(item for item in catalog["providers"] if item["id"] == "deepseek")
    return {"revision": revision, "mode": "builtin", "provider": provider["id"],
            "model": provider["models"][0]["id"], "base_url": "", "api": "", "api_key": key}


def custom(revision=0, key=KEY):
    return {"revision": revision, "mode": "custom", "provider": "synthetic", "model": "synthetic-model",
            "base_url": "https://model.example.test/v1", "api": "openai-completions", "api_key": key}


@pytest.fixture
def settings(monkeypatch, tmp_path):
    directory = tmp_path / "private-model"
    monkeypatch.setenv("BRIEFING_PI_SETTINGS_DIR", str(directory))
    monkeypatch.setenv("BRIEFING_GENERATOR", "pi")
    app = create_app(tmp_path / "state", TOKENS)
    with TestClient(app, base_url="https://briefing.example.test") as client:
        yield client, app, directory


@pytest.mark.parametrize("role", [None, "read", "ingest", "worker"])
def test_catalog_and_model_settings_require_management_authority(settings, role):
    client, _, _ = settings
    auth = {} if role is None else headers(role)
    assert client.get(MANAGE + "/model-catalog", headers=auth).status_code == 401
    assert client.get(MANAGE + "/model-settings", headers=auth).status_code == 401
    assert client.put(MANAGE + "/model-settings", headers=auth, json=custom()).status_code == 401


def test_unconfigured_and_real_catalog_have_no_synthetic_provider_choices(settings):
    client, _, directory = settings
    current = client.get(MANAGE + "/model-settings", headers=headers())
    assert current.status_code == 200
    assert current.json() == {"enabled": True, "revision": 0, "configured": False, "mode": "builtin",
        "provider": "", "model": "", "base_url": "", "api": "", "api_key_configured": False, "source": "unconfigured"}
    catalog = client.get(MANAGE + "/model-catalog", headers=headers()).json()
    assert catalog["version"] == "1.4.0" and "/v1.4.0/" in catalog["source"]
    ids = {item["id"] for item in catalog["providers"]}
    assert {"minimax", "minimax-cn", "moonshotai", "moonshotai-cn", "deepseek"} <= ids
    assert "synthetic" not in ids and "openai-codex" not in ids and "azure" not in ids
    assert all(item["models"] and all(set(model) == {"id", "name"} for model in item["models"]) for item in catalog["providers"])
    assert catalog["custom_apis"][0]["id"] == "openai-completions"
    assert not (directory / "pi-model-settings.json").exists()


def test_save_is_private_persistent_and_performs_no_model_request(settings, monkeypatch, tmp_path):
    client, app, directory = settings
    monkeypatch.setattr("briefing.generators._execute", lambda *args, **kwargs: pytest.fail("saving must not invoke a model"))
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=builtin())
    assert response.status_code == 200
    saved = response.json()
    assert saved["configured"] is True and saved["api_key_configured"] is True
    assert saved["revision"] == 1 and saved["source"] == "web" and "api_key" not in saved
    assert KEY not in response.text and response.headers["cache-control"] == "no-store"
    target = directory / "pi-model-settings.json"
    assert json.loads(target.read_text())["api_key"] == KEY
    assert KEY not in app.state.store.path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    other = create_app(tmp_path / "state-reopened", TOKENS)
    with TestClient(other, base_url="https://briefing.example.test") as reopened:
        assert reopened.get(MANAGE + "/model-settings", headers=headers()).json() == saved


def test_stale_revision_cannot_replace_or_disclose_saved_key(settings):
    client, _, directory = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    before = (directory / "pi-model-settings.json").read_bytes()
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=custom(key="another-synthetic-key"))
    assert response.status_code == 409 and response.json()["detail"] == "model_settings_revision_conflict"
    assert KEY not in response.text and "another-synthetic-key" not in response.text
    assert (directory / "pi-model-settings.json").read_bytes() == before


def test_same_destination_model_change_can_retain_key(settings):
    client, _, directory = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    payload = custom(revision=1, key="")
    payload["model"] = "another-model"
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=payload)
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert json.loads((directory / "pi-model-settings.json").read_text())["api_key"] == KEY


@pytest.mark.parametrize("change", [
    {"provider": "different-provider"}, {"base_url": "https://other.example.test/v1"},
    {"mode": "builtin", "provider": "deepseek", "model": "deepseek-chat", "base_url": "", "api": ""},
])
def test_changed_credential_destination_requires_a_fresh_key(settings, change):
    client, _, directory = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    before = (directory / "pi-model-settings.json").read_bytes()
    payload = {**custom(revision=1, key=""), **change}
    # Use a catalog model rather than relying on a provider alias for the mode change.
    if payload["mode"] == "builtin":
        payload = builtin(revision=1, key="")
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=payload)
    assert response.status_code == 422 and response.json()["detail"] == "api_key_required_for_destination"
    assert (directory / "pi-model-settings.json").read_bytes() == before


@pytest.mark.parametrize("change", [
    {"base_url": "http://model.example.test/v1"}, {"base_url": "https://user:password@model.example.test"},
    {"base_url": "https://model.example.test?key=private"}, {"base_url": "https://model.example.test/#frag"},
    {"base_url": "https://model.example.test:99999"}, {"provider": "../escape"}, {"model": "--tool"},
    {"api": "shell-command"}, {"api_key": "!execute"}, {"api_key": "two\nlines"},
])
def test_invalid_model_request_is_sanitized_and_leaves_no_secret_file(settings, change):
    client, _, directory = settings
    response = client.put(MANAGE + "/model-settings", headers=headers(), json={**custom(), **change})
    assert response.status_code == 422
    assert KEY not in response.text and "password" not in response.text and "private" not in response.text
    assert not (directory / "pi-model-settings.json").exists()


def test_builtin_provider_and_model_must_exist_in_real_catalog(settings):
    client, _, _ = settings
    for payload in ({**builtin(), "provider": "synthetic"}, {**builtin(), "model": "invented-model"},
                    {**builtin(), "base_url": "https://redirect.example.test/v1"}):
        assert client.put(MANAGE + "/model-settings", headers=headers(), json=payload).status_code == 422


def test_web_model_settings_need_explicit_server_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIEFING_PI_SETTINGS_DIR", raising=False)
    monkeypatch.delenv("PI_API_KEY_FILE", raising=False)
    with TestClient(create_app(tmp_path / "state", TOKENS), base_url="https://briefing.example.test") as client:
        assert client.get(MANAGE + "/model-settings", headers=headers()).json()["enabled"] is False
        assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 503


def test_cookie_settings_write_requires_same_origin(settings):
    client, _, _ = settings
    login = client.post(MANAGE + "/session", headers={"Origin": "https://briefing.example.test"}, json={"token": TOKENS["manage"]})
    assert login.status_code == 200
    assert client.get(MANAGE + "/model-settings").status_code == 200
    assert client.put(MANAGE + "/model-settings", json=custom()).status_code == 403
    assert client.put(MANAGE + "/model-settings", headers={"Origin": "https://evil.example.test"}, json=custom()).status_code == 403
    assert client.put(MANAGE + "/model-settings", headers={"Origin": "https://briefing.example.test"}, json=custom()).status_code == 200


def test_concurrent_settings_updates_do_not_silently_overwrite(settings):
    client, _, _ = settings
    def save(_):
        return client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [200, 409]


def test_failed_atomic_replace_preserves_previous_configuration_and_key(settings, monkeypatch):
    client, _, directory = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    before = (directory / "pi-model-settings.json").read_bytes()
    def fail_replace(*args):
        raise OSError("synthetic private storage diagnostic")
    monkeypatch.setattr("briefing.model_settings.os.replace", fail_replace)
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=custom(revision=1, key="replacement-key"))
    assert response.status_code == 503 and response.json() == {"detail": "model_settings_unavailable"}
    assert (directory / "pi-model-settings.json").read_bytes() == before
    assert sorted(path.name for path in directory.iterdir()) == ["pi-model-settings.json"]


def test_legacy_environment_settings_are_reported_without_reading_back_key(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIEFING_PI_SETTINGS_DIR", raising=False)
    key = tmp_path / "legacy.key"
    key.write_text(KEY)
    monkeypatch.setenv("PI_PROVIDER", "synthetic")
    monkeypatch.setenv("PI_MODEL", "synthetic-model")
    monkeypatch.setenv("PI_BASE_URL", "https://legacy.example.test/v1")
    monkeypatch.setenv("PI_API_KEY_FILE", str(key))
    with TestClient(create_app(tmp_path / "state", TOKENS), base_url="https://briefing.example.test") as client:
        response = client.get(MANAGE + "/model-settings", headers=headers())
        assert response.status_code == 200 and KEY not in response.text
        assert response.json()["source"] == "environment" and response.json()["configured"] is True
        assert response.json()["enabled"] is False and "api_key" not in response.json()


def test_web_key_is_a_literal_and_never_reappears_in_management_responses(settings, monkeypatch, tmp_path):
    client, _, directory = settings
    literal = "synthetic$UNSET_VARIABLE-${UNSET_VARIABLE}-$$-$!"
    response = client.put(MANAGE + "/model-settings", headers=headers(), json=custom(key=literal))
    assert response.status_code == 200 and literal not in response.text
    assert literal not in client.get(MANAGE + "/model-settings", headers=headers()).text
    def execute(command, run_dir, environment, timeout):
        auth = json.loads((Path(environment["PI_CODING_AGENT_DIR"]) / "auth.json").read_text())
        assert auth["synthetic"]["key"] == "synthetic$$UNSET_VARIABLE-$${UNSET_VARIABLE}-$$$$-$$!"
        return events()
    monkeypatch.setattr("briefing.generators._execute", execute)
    assert run_generator(source(), "unused", tmp_path / "worker") == draft()
    assert json.loads((directory / "pi-model-settings.json").read_text())["api_key"] == literal


def test_unconfigured_web_worker_does_not_claim_jobs_or_fall_back_to_environment(settings, monkeypatch, tmp_path):
    key = tmp_path / "legacy.key"
    key.write_text(KEY)
    monkeypatch.setenv("PI_PROVIDER", "synthetic")
    monkeypatch.setenv("PI_MODEL", "synthetic-model")
    monkeypatch.setenv("PI_API_KEY_FILE", str(key))
    with httpx.Client(base_url="https://api.example.test/", transport=httpx.MockTransport(
            lambda request: pytest.fail("must not claim a job until model settings exist"))) as client:
        assert run_once(client, "unused", tmp_path / "worker") == "model_not_configured"


def test_corrupt_settings_fail_before_a_job_is_claimed(settings, tmp_path):
    _, _, directory = settings
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pi-model-settings.json").write_text("{broken")
    with httpx.Client(base_url="https://api.example.test/", transport=httpx.MockTransport(
            lambda request: pytest.fail("must not claim with corrupt model settings"))) as client:
        assert run_once(client, "unused", tmp_path / "worker") == "model_settings_unavailable"


def test_worker_uses_one_saved_snapshot_for_a_job_and_does_not_mutate_environment(settings, monkeypatch, tmp_path):
    client, _, _ = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    before = dict(os.environ)
    observed = []
    def execute(command, directory, environment, timeout):
        config = json.loads((Path(environment["PI_CODING_AGENT_DIR"]) / "models.json").read_text())
        auth = json.loads((Path(environment["PI_CODING_AGENT_DIR"]) / "auth.json").read_text())
        observed.append((config["providers"]["synthetic"]["baseUrl"], auth["synthetic"]["key"]))
        return events()
    monkeypatch.setattr("briefing.generators._execute", execute)
    def handler(request):
        if request.url.path.endswith("jobs/next"):
            changed = {**custom(revision=1, key="new-synthetic-key"), "base_url": "https://next.example.test/v1"}
            assert client.put(MANAGE + "/model-settings", headers=headers(), json=changed).status_code == 200
            return httpx.Response(200, json=source())
        assert request.url.path.endswith("editions")
        return httpx.Response(200, json={"status": "published"})
    with httpx.Client(base_url="https://api.example.test/", transport=httpx.MockTransport(handler)) as worker:
        assert run_once(worker, "unused", tmp_path / "worker") == "published"
    assert observed == [("https://model.example.test/v1", KEY)]
    assert dict(os.environ) == before


def test_configured_raw_only_does_not_call_pi(settings, monkeypatch, tmp_path):
    client, _, _ = settings
    assert client.put(MANAGE + "/model-settings", headers=headers(), json=custom()).status_code == 200
    monkeypatch.setattr("briefing.generators._execute", lambda *args: pytest.fail("raw mode must not invoke Pi"))
    assert run_generator(source("raw"), "unused", tmp_path)["sections"]
