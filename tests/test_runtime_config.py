"""Portable configuration checks use temporary files and never open a network client."""
import json
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


def custom_sources(tmp_path):
    policy = tmp_path / 'source-policy.json'
    policy.write_text(json.dumps({'version': 1, 'sources': [{'name': '每日科学小课', 'kind': 'daily',
        'event_type': '定时结果', 'narration_detail': 'detailed'}]}, ensure_ascii=False), encoding='utf-8')
    preferences = tmp_path / 'initial-preferences.json'
    preferences.write_text(json.dumps({'revision': 1, 'format': 'mixed', 'work_enabled': False, 'sources': {
        '每日科学小课': {'category': 'learning', 'mode': 'processed', 'narration_detail': 'detailed', 'note': ''}}},
        ensure_ascii=False), encoding='utf-8')
    return policy, preferences


@pytest.mark.parametrize('backend', ['codex', 'pi'])
def test_configuration_connects_confirmed_sources_and_selected_generator(tmp_path, backend):
    policy, preferences = custom_sources(tmp_path)
    key_file = tmp_path / 'provider.key'
    key_file.write_text('synthetic-provider-key-do-not-log')
    output = tmp_path / 'credentials'
    args = ['--generator', backend, '--source-policy', str(policy), '--initial-preferences', str(preferences)]
    if backend == 'pi':
        args += ['--pi-provider', 'example-provider', '--pi-model', 'example-model', '--pi-bin', '/opt/pi/bin/pi',
                 '--pi-api-key-file', str(key_file), '--pi-base-url', 'https://api.example.com/v1']
    result = generate(output, '--base-url', 'https://briefing.example.com/briefing/v1', *args)
    assert result.returncode == 0, result.stderr
    api_env = (output / 'api.env').read_text(encoding='utf-8')
    worker_env = (output / 'worker.env').read_text(encoding='utf-8')
    policy_value = str(policy.resolve()).replace('\\', '\\\\')
    preferences_value = str(preferences.resolve()).replace('\\', '\\\\')
    assert f'BRIEFING_SOURCE_POLICY_PATH="{policy_value}"' in api_env
    assert f'BRIEFING_SOURCE_POLICY_PATH="{policy_value}"' in worker_env
    assert f'BRIEFING_INITIAL_PREFERENCES_PATH="{preferences_value}"' in api_env
    assert 'BRIEFING_INITIAL_PREFERENCES_PATH' not in worker_env
    assert f'BRIEFING_GENERATOR="{backend}"' in worker_env
    if backend == 'pi':
        assert 'PI_PROVIDER="example-provider"' in worker_env
        assert 'PI_MODEL="example-model"' in worker_env
        assert 'PI_BIN="/opt/pi/bin/pi"' in worker_env
        assert 'PI_BASE_URL="https://api.example.com/v1"' in worker_env
        assert 'PI_API_KEY_FILE=' in worker_env
    assert 'synthetic-provider-key-do-not-log' not in api_env + worker_env + result.stdout + result.stderr


@pytest.mark.parametrize('missing', ['--pi-provider', '--pi-model', '--pi-api-key-file'])
def test_pi_missing_configuration_fails_before_writing_any_credentials(tmp_path, missing):
    key = tmp_path / 'provider.key'
    key.write_text('synthetic-key')
    pairs = {'--pi-provider': 'example', '--pi-model': 'example-model', '--pi-api-key-file': str(key)}
    args = [part for option, value in pairs.items() if option != missing for part in (option, value)]
    output = tmp_path / 'credentials'
    result = generate(output, '--base-url', 'https://briefing.example.com/briefing/v1', '--generator', 'pi', *args)
    assert result.returncode == 2
    assert not output.exists()


def test_custom_preferences_require_policy_and_reject_mismatched_sources_before_write(tmp_path):
    policy, preferences = custom_sources(tmp_path)
    output = tmp_path / 'credentials'
    result = generate(output, '--base-url', 'https://briefing.example.com/briefing/v1',
                      '--initial-preferences', str(preferences))
    assert result.returncode == 2
    assert not output.exists()
    data = json.loads(preferences.read_text(encoding='utf-8'))
    data['sources']['另一来源'] = data['sources'].pop('每日科学小课')
    preferences.write_text(json.dumps(data), encoding='utf-8')
    result = generate(output, '--base-url', 'https://briefing.example.com/briefing/v1',
                      '--source-policy', str(policy), '--initial-preferences', str(preferences))
    assert result.returncode == 2
    assert not output.exists()


def test_invalid_pi_endpoint_and_missing_key_path_preserve_prior_configuration(tmp_path):
    output = tmp_path / 'credentials'
    assert generate(output, '--base-url', 'https://briefing.example.com/briefing/v1').returncode == 0
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    result = generate(output, '--base-url', 'https://briefing.example.com/briefing/v1', '--generator', 'pi',
                      '--pi-provider', 'example', '--pi-model', 'example-model',
                      '--pi-api-key-file', str(tmp_path / 'missing.key'), '--pi-base-url', 'https://user:secret@example.com/v1')
    assert result.returncode == 2
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


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
