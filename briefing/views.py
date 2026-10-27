"""Select cached narration using current preferences and actual message provenance."""
from __future__ import annotations

from .models import LearningSummary, Section, subject_task
from .preferences import validate_preferences_snapshot
from .source_policy import SOURCE_ORDER


def _group_sections(raw: list[dict], tasks: dict[str, str], model: type[Section]) -> dict[str, list[dict]]:
    if not isinstance(raw, list):
        raise ValueError("invalid_view_sources")
    grouped: dict[str, list[dict]] = {}
    for item in raw:
        section = model.model_validate(item).model_dump(mode="json")
        identifiers = section["source_ids"]
        sources = {tasks[identifier] for identifier in identifiers}
        if len(sources) != 1 or len(identifiers) != len(set(identifiers)):
            raise ValueError("invalid_view_sources")
        grouped.setdefault(sources.pop(), []).append(section)
    return grouped


def select_view(edition: dict, messages: list[dict], preferences: dict, view: str) -> list[dict]:
    """Return independent Section dictionaries without generating or editing content.

    Daily reads substitute one cached overview for each currently classified
    learning source. A legacy edition without that overview gets a deterministic
    invitation to request the complete lesson, never a truncated lesson presented
    as a summary. Unknown views or untrusted provenance fail closed with ValueError.
    """
    if view not in ("daily", "learning", "work", "full"):
        raise ValueError("invalid_view")
    try:
        current = validate_preferences_snapshot(preferences)
        if not isinstance(messages, list):
            raise ValueError("invalid_view_sources")
        tasks: dict[str, str] = {}
        for message in messages:
            identifier = message["id"]
            if not isinstance(identifier, str) or not identifier or identifier in tasks:
                raise ValueError("invalid_view_sources")
            tasks[identifier] = subject_task(message["subject"])
        sections = _group_sections(edition["sections"], tasks, Section)
        summaries = _group_sections(edition.get("learning_summaries", []), tasks, LearningSummary)
        if any(len(items) != 1 for items in summaries.values()):
            raise ValueError("invalid_view_sources")

        selected: list[dict] = []
        for source in sorted(sections, key=SOURCE_ORDER.__getitem__):
            category = current["sources"][source]["category"]
            if category == "work" and not current["work_enabled"]:
                continue
            if view in ("learning", "work") and category != view:
                continue
            if view != "daily" or category != "learning":
                selected.extend(sections[source])
                continue

            # The cache can predate newly arrived mail. Compare against the cached
            # source coverage, not all current mail, so the last good edition stays usable.
            source_ids = {identifier for section in sections[source] for identifier in section["source_ids"]}
            if source in summaries:
                summary = summaries[source][0]
                if set(summary["source_ids"]) != source_ids:
                    raise ValueError("invalid_view_sources")
                selected.append(summary)
            else:
                selected.append({
                    "title": source,
                    "topic": "学习提示",
                    "text": f"今天的学习材料是《{source}》，可说今天应该学什么听取完整内容。",
                    "source_ids": sorted(source_ids),
                })
        return selected
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("invalid_view_sources") from None
