from __future__ import annotations

from datetime import date
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .source_policy import SOURCE_RULES, validate_event_marker

MessageID = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]
Fingerprint = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RebuildID = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
SUBJECT = re.compile(r"^\[每日早报\]\[([^\[\]\r\n]{1,80})\]\[(\d{4}-\d{2}-\d{2})\]$")
MAX_SECTIONS = 32
MAX_SECTION_CHARS = 600


def subject_task(subject: str) -> str:
    match = SUBJECT.fullmatch(subject)
    if not match or match.group(1) not in SOURCE_RULES:
        raise ValueError("report_subject_not_allowed")
    date.fromisoformat(match.group(2))
    return match.group(1)


def subject_date(subject: str) -> str:
    subject_task(subject)
    return SUBJECT.fullmatch(subject).group(2)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("blank_string")
        return value


class Message(StrictModel):
    id: MessageID
    subject: str = Field(min_length=1, max_length=240)
    received_at: AwareDatetime
    body: str = Field(min_length=1, max_length=80000)
    source_url: str | None = Field(default=None, max_length=2000)

    @field_validator("subject")
    @classmethod
    def allowed_subject(cls, value: str) -> str:
        subject_date(value)
        return value

    @model_validator(mode="after")
    def allowed_event(self):
        validate_event_marker(self.body, subject_task(self.subject))
        return self

    @field_validator("source_url")
    @classmethod
    def safe_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("source_url_must_be_https")
        return value


class Ingest(StrictModel):
    messages: list[Message] = Field(min_length=1, max_length=50)


class Section(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=MAX_SECTION_CHARS)
    source_ids: list[MessageID] = Field(min_length=1, max_length=100)


class LearningSummary(Section):
    text: str = Field(min_length=1, max_length=250)


class Draft(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    sections: list[Section] = Field(min_length=1, max_length=MAX_SECTIONS)
    warnings: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(max_length=20)
    learning_summaries: list[LearningSummary] = Field(default_factory=list, max_length=len(SOURCE_RULES))


class Edition(Draft):
    date: date
    generated_at: AwareDatetime
    source_message_ids: list[MessageID] = Field(min_length=1, max_length=100)
    source_dates: list[date] = Field(min_length=1, max_length=100)
    fingerprint: Fingerprint
    rebuild_id: RebuildID | None = None
    lease_id: RebuildID | None = None
    preferences_revision: int | None = Field(default=None, ge=1, strict=True)
    format: Literal["mixed"] | None = None


class Failure(StrictModel):
    date: date
    fingerprint: Fingerprint
    code: Literal["timeout", "codex_failed", "invalid_output", "input_too_large", "publish_failed", "worker_interrupted"]
    rebuild_id: RebuildID | None = None
    lease_id: RebuildID | None = None
    preferences_revision: int | None = Field(default=None, ge=1, strict=True)


class Rebuild(StrictModel):
    date: date


class ReadRequest(StrictModel):
    date: str = "today"
    view: Literal["daily", "learning", "work", "full"] = "daily"
    section: int = Field(default=1, ge=1, le=1000)
    topic: str = Field(default="all", min_length=1, max_length=40)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if value not in ("today", "latest"):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("date_must_be_today_latest_or_iso_date")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def legacy_tool_view(self):
        # The published three-parameter tool cannot add a view parameter.
        aliases = {"__view_daily": "daily", "__view_learning": "learning",
                   "__view_work": "work", "__view_full": "full"}
        selected = aliases.get(self.topic)
        if selected is not None:
            if self.view != "daily" and self.view != selected:
                raise ValueError("view_topic_alias_conflict")
            self.view, self.topic = selected, "all"
        return self
