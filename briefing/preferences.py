"""Validated cloud narration preferences; source identity stays in the policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .source_policy import SOURCE_POLICY, SOURCE_RULES

NarrationDetail = Literal["simple", "standard", "detailed"]
NarrationMode = Literal["raw", "processed", "podcast"]
SourceCategory = Literal["news", "learning", "work"]
INITIAL_LEARNING_SOURCES = frozenset({"每日知识练习", "外语练习", "技术周报", "每周学习复盘"})


class SourcePreference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: NarrationMode
    category: SourceCategory
    narration_detail: NarrationDetail
    note: str = Field(max_length=200)


class PreferencesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(ge=1)
    format: Literal["mixed"]
    work_enabled: bool
    sources: dict[str, SourcePreference]

    @field_validator("sources")
    @classmethod
    def all_canonical_sources(cls, value, info: ValidationInfo):
        expected = (info.context or {}).get("source_names", set(SOURCE_RULES))
        if set(value) != set(expected):
            raise ValueError("preferences_sources_mismatch")
        return value


class SourcePreferenceUpdate(SourcePreference):
    name: str


class UpdatePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=1)
    work_enabled: bool
    sources: list[SourcePreferenceUpdate]

    @field_validator("sources")
    @classmethod
    def all_canonical_sources_once(cls, value):
        names = [source.name for source in value]
        if len(names) != len(set(names)) or set(names) != set(SOURCE_RULES):
            raise ValueError("preferences_sources_mismatch")
        return value


def default_preferences() -> dict:
    return {"revision": 1, "format": "mixed", "work_enabled": False, "sources": {
        source["name"]: {"mode": "processed", "category": "learning" if source["name"] in INITIAL_LEARNING_SOURCES else "news",
                         "narration_detail": "detailed" if source["name"] in INITIAL_LEARNING_SOURCES else source["narration_detail"], "note": ""}
        for source in SOURCE_POLICY["sources"]
    }}


def validate_preferences_snapshot(raw, *, source_names=None) -> dict:
    context = {"source_names": source_names} if source_names is not None else None
    return PreferencesSnapshot.model_validate(raw, context=context).model_dump(mode="json")


def initial_preferences() -> dict:
    """Read a deployment seed only when the store has no saved preferences."""
    configured = os.environ.get("BRIEFING_INITIAL_PREFERENCES_PATH")
    if not configured:
        return default_preferences()
    return load_initial_preferences(Path(configured))


def load_initial_preferences(path: Path, *, source_names=None) -> dict:
    """Validate a setup seed before writing credentials or initializing state."""
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError("oversized_seed")
        preferences = validate_preferences_snapshot(json.loads(path.read_text(encoding="utf-8")), source_names=source_names)
        if preferences["revision"] != 1:
            raise ValueError("initial_revision_must_be_one")
        return preferences
    except (OSError, ValueError, TypeError):
        # Never echo the rejected private preference text in startup diagnostics.
        raise RuntimeError("invalid_initial_preferences") from None
