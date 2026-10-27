"""Validated cloud narration preferences; source identity stays in the policy."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    def all_canonical_sources(cls, value):
        if set(value) != set(SOURCE_RULES):
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


def validate_preferences_snapshot(raw) -> dict:
    return PreferencesSnapshot.model_validate(raw).model_dump(mode="json")
