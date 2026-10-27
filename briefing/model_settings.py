"""Private Pi settings, separate from briefing content and never returned with keys."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

SETTINGS_FILENAME = "pi-model-settings.json"
MAX_SETTINGS_BYTES = 100_000
PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}")


class ModelSettingsError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


class UpdateModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    mode: Literal["builtin", "custom"]
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=2000)
    api: str = Field(default="", max_length=80)
    api_key: str | None = Field(default=None, max_length=16384, repr=False)


@dataclass(frozen=True)
class PiModelSettings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    mode: str = "builtin"
    base_url: str = ""
    api: str = ""
    revision: int = 0
    source: str = "environment"

    @property
    def destination(self) -> tuple[str, str, str, str]:
        return self.mode, self.provider, self.base_url, self.api

    def public(self, *, enabled: bool) -> dict:
        return {"enabled": enabled, "revision": self.revision, "configured": True,
                "mode": self.mode, "provider": self.provider, "model": self.model,
                "base_url": self.base_url, "api": self.api,
                "api_key_configured": True, "source": self.source}


def configured_directory() -> Path | None:
    value = os.environ.get("BRIEFING_PI_SETTINGS_DIR", "")
    return Path(value).expanduser() if value else None


@lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        value = json.loads(Path(__file__).with_name("pi_model_catalog.json").read_text(encoding="utf-8"))
        if value["version"] != "1.4.0" or not value["providers"]:
            raise ValueError("invalid_catalog")
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ModelSettingsError("model_catalog_unavailable", 503) from None


def model_catalog() -> dict:
    return deepcopy(_catalog())


def validate_api_key(value: str) -> str:
    if (not value or len(value) > 16384 or value.startswith("!")
            or any(char.isspace() or char == "\0" for char in value)):
        raise ModelSettingsError("invalid_api_key")
    return value


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(char.isspace() or char == "\0" for char in value)):
            raise ValueError("invalid_url")
        parsed.port
    except ValueError:
        raise ModelSettingsError("invalid_model_endpoint") from None
    return value.rstrip("/")


def selection(payload: UpdateModelSettings) -> tuple[str, str]:
    if not PROVIDER_ID.fullmatch(payload.provider):
        raise ModelSettingsError("invalid_model_provider")
    if payload.mode == "builtin":
        provider = next((item for item in _catalog()["providers"] if item["id"] == payload.provider), None)
        if (provider is None or payload.model not in {model["id"] for model in provider["models"]}
                or payload.base_url or payload.api):
            raise ModelSettingsError("invalid_builtin_model")
        return "", ""
    if not MODEL_ID.fullmatch(payload.model) or payload.api != "openai-completions":
        raise ModelSettingsError("invalid_custom_model")
    return validate_base_url(payload.base_url), payload.api


def read_saved_settings(directory: Path) -> PiModelSettings | None:
    path = Path(directory) / SETTINGS_FILENAME
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise ModelSettingsError("model_settings_unavailable", 503) from None
    try:
        if len(content) > MAX_SETTINGS_BYTES:
            raise ValueError("oversized_settings")
        value = json.loads(content)
        if (not isinstance(value, dict) or type(value.get("version")) is not int or value.pop("version", None) != 1
                or type(value.get("revision")) is not int or value["revision"] < 1):
            raise ValueError("invalid_settings")
        payload = UpdateModelSettings.model_validate(value)
        base_url, api = selection(payload)
        key = validate_api_key(payload.api_key or "")
        return PiModelSettings(payload.provider, payload.model, key, payload.mode, base_url, api,
                               payload.revision, "web")
    except (ModelSettingsError, ValueError, TypeError, KeyError):
        raise ModelSettingsError("model_settings_unavailable", 503) from None


def environment_settings() -> PiModelSettings:
    """Preserve the explicit legacy file/environment path when web settings are disabled."""
    provider, model = os.environ.get("PI_PROVIDER", ""), os.environ.get("PI_MODEL", "")
    key_path = os.environ.get("PI_API_KEY_FILE", "")
    try:
        if (not PROVIDER_ID.fullmatch(provider) or not model or len(model) > 200
                or any(char.isspace() or char == "\0" for char in model) or not key_path):
            raise ValueError("incomplete_environment")
        with Path(key_path).open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("invalid_key_file")
        key = validate_api_key(raw.decode("utf-8").strip())
        base_url = os.environ.get("PI_BASE_URL", "")
        base_url = validate_base_url(base_url) if base_url else ""
        return PiModelSettings(provider, model, key, "custom" if base_url else "builtin", base_url,
                               "openai-completions" if base_url else "")
    except (OSError, ValueError, ModelSettingsError):
        raise ModelSettingsError("generation_failed", 503) from None


def load_pi_settings() -> PiModelSettings | None:
    directory = configured_directory()
    return read_saved_settings(directory) if directory is not None else environment_settings()


class ModelSettingsStore:
    """One API process writes under a lock; workers consume atomic, read-only snapshots."""
    def __init__(self, directory: Path | None):
        self.directory = directory
        self.lock = RLock()

    def public(self) -> dict:
        if self.directory is not None:
            snapshot = read_saved_settings(self.directory)
        else:
            try:
                snapshot = environment_settings()
            except ModelSettingsError:
                snapshot = None
        if snapshot is not None:
            return snapshot.public(enabled=self.directory is not None)
        return {"enabled": self.directory is not None, "revision": 0, "configured": False,
                "mode": "builtin", "provider": "", "model": "", "base_url": "", "api": "",
                "api_key_configured": False, "source": "unconfigured"}

    def update(self, payload: UpdateModelSettings) -> dict:
        if self.directory is None:
            raise ModelSettingsError("model_settings_disabled", 503)
        base_url, api = selection(payload)
        with self.lock:
            current = read_saved_settings(self.directory)
            revision = current.revision if current else 0
            if payload.revision != revision:
                raise ModelSettingsError("model_settings_revision_conflict", 409)
            destination = (payload.mode, payload.provider, base_url, api)
            if payload.api_key in (None, ""):
                if current is None or current.destination != destination:
                    raise ModelSettingsError("api_key_required_for_destination")
                key = current.api_key
            else:
                key = validate_api_key(payload.api_key)
            snapshot = PiModelSettings(payload.provider, payload.model, key, payload.mode, base_url, api,
                                       revision + 1, "web")
            value = {"version": 1, "revision": snapshot.revision, "mode": snapshot.mode,
                     "provider": snapshot.provider, "model": snapshot.model, "base_url": snapshot.base_url,
                     "api": snapshot.api, "api_key": snapshot.api_key}
            temporary = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self.directory, 0o700)
                fd, temporary = tempfile.mkstemp(prefix=".pi-settings-", suffix=".tmp", dir=self.directory)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.directory / SETTINGS_FILENAME)
            except OSError:
                raise ModelSettingsError("model_settings_unavailable", 503) from None
            finally:
                if temporary is not None:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass  # Preserve the sanitized write failure if cleanup also fails.
            return snapshot.public(enabled=True)
