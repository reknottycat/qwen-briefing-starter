"""Fresh processes observe the same private policy as a real service startup."""
import json
import os
from pathlib import Path
import subprocess
import sys


def configured_process(tmp_path, script, *, preferences=None, policy_path=None):
    policy = {"version": 1, "sources": [{"name": "我的科学课", "kind": "daily",
              "event_type": "定时结果", "narration_detail": "detailed"}]}
    if policy_path is None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    prefs = preferences or {"revision": 1, "format": "mixed", "work_enabled": False,
        "sources": {"我的科学课": {"mode": "raw", "category": "learning",
                  "narration_detail": "detailed", "note": "保留原文"}}}
    seed = tmp_path / "initial.json"
    seed.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BRIEFING_")}
    environment.update(BRIEFING_SOURCE_POLICY_PATH=str(policy_path),
                       BRIEFING_INITIAL_PREFERENCES_PATH=str(seed),
                       BRIEFING_DATA_DIR=str(tmp_path / "state"),
                       PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", script], env=environment,
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


def test_custom_install_ingests_generates_and_reads_exact_raw_learning(tmp_path):
    result = configured_process(tmp_path, '''
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from briefing.api import create_app, PREFIX
from briefing.worker import run_codex, validate_draft
from pathlib import Path
import os
tokens = {role: role + "-synthetic-" + "x" * 32 for role in ("read", "worker", "ingest", "manage")}
app = create_app(tokens=tokens, clock=lambda: datetime(2026,10,27,1,tzinfo=timezone.utc))
def auth(role): return {"Authorization": "Bearer " + tokens[role]}
with TestClient(app) as client:
    prefs = app.state.store.state["preferences"]
    assert list(prefs["sources"]) == ["我的科学课"]
    assert prefs["sources"]["我的科学课"]["category"] == "learning"
    body = "第一课：每天观察月亮。\\n请记录方向，尚无标准答案。"
    message = {"id":"synthetic-1", "subject":"[每日早报][我的科学课][2026-10-27]",
               "received_at":"2026-10-27T00:30:00Z", "body":"早报事件类型：定时结果\\n" + body,
               "source_url":"https://example.com/synthetic"}
    assert client.post(PREFIX+"/ingest", headers=auth("ingest"), json={"messages":[message]}).status_code == 200
    job = client.get(PREFIX+"/jobs/next", headers=auth("worker")).json()
    draft = run_codex(job, "must-not-run", Path(os.environ["BRIEFING_DATA_DIR"]) / "unused")
    edition = validate_draft(job, draft)
    edition.generated_at = app.state.store.clock()
    published = client.post(PREFIX+"/editions", headers=auth("worker"), json=edition.model_dump(mode="json"))
    assert published.status_code == 200, published.text
    reply = client.get(PREFIX+"/read", headers=auth("read"), params={"date":"2026-10-27", "view":"learning"}).json()
    assert reply["status"] == "ready", reply
    assert body in reply["full_spoken_text"]
    assert client.get(PREFIX+"/read").status_code == 401
print("custom-source-ingest-publish-read-passed")
''')
    assert result.returncode == 0, result.stderr
    assert "custom-source-ingest-publish-read-passed" in result.stdout


def test_initial_preferences_do_not_replace_saved_changes_on_restart(tmp_path):
    first = configured_process(tmp_path, '''
from pathlib import Path
from briefing.store import Store
import os
store = Store(Path(os.environ["BRIEFING_DATA_DIR"]))
state = store.state
state["preferences"]["revision"] = 2
state["preferences"]["sources"]["我的科学课"]["note"] = "用户后来修改"
store._commit(state)
''')
    assert first.returncode == 0, first.stderr
    second = configured_process(tmp_path, '''
from pathlib import Path
from briefing.store import Store
import os
store = Store(Path(os.environ["BRIEFING_DATA_DIR"]))
assert store.state["preferences"]["revision"] == 2
assert store.state["preferences"]["sources"]["我的科学课"]["note"] == "用户后来修改"
''')
    assert second.returncode == 0, second.stderr


def test_invalid_initial_preferences_write_no_state(tmp_path):
    result = configured_process(tmp_path, '''
from pathlib import Path
from briefing.store import Store
import os
Store(Path(os.environ["BRIEFING_DATA_DIR"]))
''', preferences={"revision": 1, "sources": {"different": {}}, "format": "mixed", "work_enabled": False})
    assert result.returncode != 0
    assert not (tmp_path / "state" / "state.json").exists()


def test_missing_explicit_policy_does_not_fall_back_to_sample_sources(tmp_path):
    result = configured_process(tmp_path, "import briefing.source_policy", policy_path=tmp_path / "missing.json")
    assert result.returncode != 0


def test_existing_incompatible_sources_fail_without_replacing_state(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = {"version": 1, "messages": {}, "editions": {}, "jobs": {},
             "preferences": {"revision": 4, "format": "mixed", "work_enabled": False,
                             "sources": {"旧来源": {"mode": "raw", "category": "news", "narration_detail": "simple", "note": ""}}}}
    path = state_dir / "state.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    result = configured_process(tmp_path, '''
from pathlib import Path
from briefing.store import Store
import os
Store(Path(os.environ["BRIEFING_DATA_DIR"]))
''')
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_first_install_seed_never_mutates_an_existing_legacy_state(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    path = directory / "state.json"
    original = {"version": 1, "messages": {"old": {
        "id": "old", "subject": "[每日早报][旧任务][2026-10-26]", "body": "旧资料"}},
        "editions": {}, "jobs": {}}
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    result = configured_process(tmp_path, '''
from pathlib import Path
from briefing.store import Store
import os
Store(Path(os.environ["BRIEFING_DATA_DIR"]))
''')
    assert result.returncode != 0
    assert "initial_preferences_require_new_state" in result.stderr
    assert path.read_bytes() == before
