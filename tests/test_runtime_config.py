"""Portable configuration checks use temporary files and never open a network client."""
from pathlib import Path
import subprocess
import sys

import pytest

from briefing import worker

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / 'deploy' / 'create-runtime-config.py'


def generate(output, *args):
    return subprocess.run([sys.executable, str(GENERATOR), '--output-dir', str(output), *args],
                          capture_output=True, text=True, timeout=10)


def test_config_generator_requires_explicit_endpoint_before_creating_files(tmp_path):
    output = tmp_path / 'credentials'
    result = generate(output)
    assert result.returncode == 2
    assert '--base-url' in result.stderr
    assert not output.exists()


@pytest.mark.parametrize('base', ['http://example.com/briefing/v1', 'https://user:secret@example.com',
                                  'https://example.com?token=private', 'https://example.com/#fragment',
                                  'https://example.com:invalid', 'https://bad host/briefing/v1'])
def test_invalid_endpoint_writes_no_credentials(tmp_path, base):
    output = tmp_path / 'credentials'
    result = generate(output, '--base-url', base)
    assert result.returncode == 2
    assert not output.exists()


def test_configuration_roundtrip_preserves_credentials_and_unrelated_files(tmp_path):
    output = tmp_path / 'credentials'
    output.mkdir()
    unrelated = output / 'keep.txt'
    unrelated.write_text('unrelated', encoding='utf-8')
    before_mode = unrelated.stat().st_mode
    original = 'synthetic_existing_ingest_token_0000001'
    (output / 'ingest.token').write_text(original, encoding='utf-8')
    first = generate(output, '--base-url', 'https://first.example.com/briefing/v1/',
                     '--codex-bin', '/opt/Codex CLI/codex', '--data-dir', '/srv/briefing data')
    assert first.returncode == 0, first.stderr
    tokens = {path.name: path.read_bytes() for path in output.glob('*.token')}
    assert len(tokens) == len(set(tokens.values())) == 4
    assert tokens['ingest.token'].decode() == original
    assert all(len(value) >= 24 for value in tokens.values())
    for value in tokens.values():
        assert value.decode() not in first.stdout + first.stderr
    api = (output / 'api.env').read_bytes()
    worker_env = (output / 'worker.env').read_bytes()
    assert b'\r' not in api + worker_env
    assert b'BRIEFING_DATA_DIR="/srv/briefing data"\n' in api
    assert b'BRIEFING_BASE_URL="https://first.example.com/briefing/v1"\n' in worker_env
    assert b'CODEX_BIN="/opt/Codex CLI/codex"\n' in worker_env
    second = generate(output, '--base-url', 'https://second.example.com/briefing/v1')
    assert second.returncode == 0, second.stderr
    assert {path.name: path.read_bytes() for path in output.glob('*.token')} == tokens
    assert 'CODEX_BIN="codex"' in (output / 'worker.env').read_text()
    assert unrelated.read_text() == 'unrelated'
    assert unrelated.stat().st_mode == before_mode


def test_invalid_existing_credentials_are_not_overwritten(tmp_path):
    (tmp_path / 'ingest.token').write_text('too-short', encoding='utf-8')
    result = generate(tmp_path, '--base-url', 'https://example.com/briefing/v1')
    assert result.returncode == 2
    assert 'too-short' not in result.stdout + result.stderr
    assert [path.name for path in tmp_path.iterdir()] == ['ingest.token']


def test_worker_requires_endpoint_before_opening_a_client(monkeypatch):
    monkeypatch.delenv('BRIEFING_BASE_URL', raising=False)
    monkeypatch.setenv('BRIEFING_WORKER_TOKEN', 'synthetic-worker-token-000000001')
    monkeypatch.setattr(worker.httpx, 'Client', lambda **kwargs: pytest.fail('opened network client'))
    with pytest.raises(SystemExit) as result:
        worker.main(['--once'])
    assert result.value.code == 2


@pytest.mark.parametrize('binary', [None, '/opt/another-user/bin/codex'])
def test_worker_uses_supplied_endpoint_and_configurable_executable(monkeypatch, binary):
    monkeypatch.setenv('BRIEFING_BASE_URL', 'https://own.example.com/briefing/v1')
    monkeypatch.setenv('BRIEFING_WORKER_TOKEN', 'synthetic-worker-token-000000001')
    if binary is None:
        monkeypatch.delenv('CODEX_BIN', raising=False)
    else:
        monkeypatch.setenv('CODEX_BIN', binary)
    observed = {}

    class Client:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def run_once(client, executable, runtime, timeout):
        observed['executable'] = executable
        return 'idle'

    monkeypatch.setattr(worker.httpx, 'Client', Client)
    monkeypatch.setattr(worker, 'run_once', run_once)
    assert worker.main(['--once']) == 0
    assert observed['base_url'] == 'https://own.example.com/briefing/v1/'
    assert observed['executable'] == (binary or 'codex')
    assert observed['follow_redirects'] is False
