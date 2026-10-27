"""Single-process state; run Uvicorn with exactly one worker.

The lock covers both validation and atomic publication. The state is small and
read from memory on glasses requests; no generation or upstream reads occur.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from threading import RLock

from .models import Edition, Failure, Ingest, ReadRequest, Rebuild, subject_date, subject_task
from .preferences import UpdatePreferences, default_preferences, validate_preferences_snapshot
from .source_policy import SOURCE_ORDER, SOURCE_POLICY
from .views import select_view

SHANGHAI = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(messages: list[dict]) -> str:
    ordered = sorted(messages, key=lambda item: item["id"])
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, directory: Path, clock=utcnow):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.json"
        self.lock = RLock()
        self.clock = clock
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1, "messages": {}, "editions": {}, "jobs": {}}
        if self.state.get("version") != 1:
            raise RuntimeError("unsupported_state_version")
        if "preferences" not in self.state:
            state = deepcopy(self.state)
            state["preferences"] = default_preferences()
            self._commit(state)
        else:
            validate_preferences_snapshot(self.state["preferences"])

    def _commit(self, state: dict) -> None:
        fd, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            self.state = state
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _messages(self, day: str) -> list[dict]:
        return sorted((m for m in self.state["messages"].values() if m["date"] == day), key=lambda m: m["id"])

    def _preferences_view(self) -> dict:
        preferences = self.state["preferences"]
        return {"revision": preferences["revision"], "format": preferences["format"], "work_enabled": preferences["work_enabled"], "sources": [
            {"name": source["name"], "kind": source["kind"], "event_type": source["event_type"], "order": index,
             **deepcopy(preferences["sources"][source["name"]])}
            for index, source in enumerate(SOURCE_POLICY["sources"])
        ]}

    def preferences(self) -> dict:
        with self.lock:
            return self._preferences_view()

    def generation_status(self) -> dict:
        with self.lock:
            today = self.clock().astimezone(SHANGHAI).date().isoformat()
            now = self.clock().timestamp()
            messages = self._messages(today)
            edition = self.state["editions"].get(today, {})
            job = self.state["jobs"].get(today, {})
            same_sources = messages and job.get("fingerprint") == fingerprint(messages)
            if not messages:
                status = "no_sources"
            elif same_sources and job.get("preferences") == self.state["preferences"]:
                if job.get("lease_until", 0) > now:
                    status = "generating"
                elif job.get("attempts", 0) >= 3:
                    status = "blocked"
                elif job.get("next_attempt_at", 0) > now:
                    status = "retry_wait"
                else:
                    status = "queued"
            elif edition.get("fingerprint") == fingerprint(messages) and not job.get("manual_rebuild"):
                status = "ready"
            else:
                status = "queued"
            return {"date": today, "source_count": len(messages), "status": status,
                    "preferences_revision": self.state["preferences"]["revision"],
                    "edition_preferences_revision": edition.get("preferences_revision"),
                    "generated_at": edition.get("generated_at"), "error": job.get("error") if same_sources else None}

    def _queue_rebuild(self, state: dict, day: str) -> dict:
        messages = self._messages(day)
        if not messages:
            return {"status": "no_sources", "date": day}
        digest = fingerprint(messages)
        preferences = state["preferences"]
        prior = state["jobs"].get(day, {})
        if prior.get("fingerprint") == digest and prior.get("preferences") == preferences and (
                prior.get("attempts", 0) < 3 or prior.get("lease_until", 0) > self.clock().timestamp()):
            return {"status": "already_queued", "date": day, "rebuild_id": prior.get("rebuild_id")}
        rebuild_id = secrets.token_hex(16)
        state["jobs"][day] = {"fingerprint": digest, "attempts": 0, "lease_until": 0, "next_attempt_at": 0,
                              "manual_rebuild": True, "rebuild_id": rebuild_id, "preferences": deepcopy(preferences)}
        return {"status": "queued", "date": day, "rebuild_id": rebuild_id}

    def update_preferences(self, payload: UpdatePreferences) -> dict:
        with self.lock:
            current = self.state["preferences"]
            if payload.expected_revision != current["revision"]:
                raise Conflict("preferences_revision_mismatch")
            selected = {source.name: source.model_dump(exclude={"name"}, mode="json") for source in payload.sources}
            changed = selected != current["sources"] or payload.work_enabled != current["work_enabled"]
            state = deepcopy(self.state)
            if changed:
                state["preferences"] = {"revision": current["revision"] + 1, "format": "mixed",
                                        "work_enabled": payload.work_enabled, "sources": selected}
            today = self.clock().astimezone(SHANGHAI).date().isoformat()
            queued = self._queue_rebuild(state, today)
            if changed or queued["status"] == "queued":
                self._commit(state)
            return {**self._preferences_view(), "changed": changed,
                    "generation": {**self.generation_status(), "queue_status": queued["status"]}}

    def ingest(self, payload: Ingest) -> dict:
        with self.lock:
            state = deepcopy(self.state)
            accepted = duplicates = 0
            for message in payload.messages:
                entry = message.model_dump(mode="json")
                entry["date"] = subject_date(message.subject)
                if entry["date"] > self.clock().astimezone(SHANGHAI).date().isoformat():
                    raise Conflict("future_source_date")
                existing = state["messages"].get(message.id)
                if existing is not None:
                    if existing != entry:
                        raise Conflict("message_id_content_conflict")
                    duplicates += 1
                else:
                    state["messages"][message.id] = entry
                    accepted += 1
            counts: dict[str, int] = {}
            for item in state["messages"].values():
                counts[item["date"]] = counts.get(item["date"], 0) + 1
            if max(counts.values(), default=0) > 100:
                raise Conflict("daily_message_limit_exceeded")
            if accepted:
                self._commit(state)
            return {"status": "accepted", "accepted": accepted, "duplicates": duplicates}

    def rebuild(self, payload: Rebuild) -> dict:
        with self.lock:
            state = deepcopy(self.state)
            queued = self._queue_rebuild(state, payload.date.isoformat())
            if queued["status"] == "no_sources":
                raise Conflict("source_date_not_found")
            if queued["status"] == "queued":
                self._commit(state)
            return queued

    def next_job(self) -> dict:
        with self.lock:
            now = self.clock().timestamp()
            preferences = self.state["preferences"]
            for day in sorted({m["date"] for m in self.state["messages"].values()}, reverse=True):
                messages = self._messages(day)
                digest = fingerprint(messages)
                previous = self.state["jobs"].get(day, {})
                manual = previous.get("manual_rebuild", False) and previous.get("fingerprint") == digest
                same_version = previous.get("fingerprint") == digest and previous.get("preferences") == preferences
                if self.state["editions"].get(day, {}).get("fingerprint") == digest and not manual:
                    continue
                if same_version and (previous.get("attempts", 0) >= 3 or previous.get("next_attempt_at", 0) > now or previous.get("lease_until", 0) > now):
                    continue
                state = deepcopy(self.state)
                attempts = previous.get("attempts", 0) if same_version else 0
                # Count a lease as an attempt so a killed worker cannot retry forever.
                state["jobs"][day] = {"fingerprint": digest, "attempts": attempts + 1, "lease_until": now + 900,
                                      "next_attempt_at": 0, "lease_id": secrets.token_hex(16), "preferences": deepcopy(preferences)}
                if manual:
                    state["jobs"][day].update(manual_rebuild=True, rebuild_id=previous["rebuild_id"])
                self._commit(state)
                return {"status": "ready", "date": day, "fingerprint": digest, "messages": deepcopy(messages),
                        "rebuild_id": previous["rebuild_id"] if manual else None,
                        "lease_id": state["jobs"][day].get("lease_id"), "preferences": deepcopy(preferences)}
            return {"status": "idle"}

    def _require_lease(self, job: dict, fingerprint: str, rebuild_id: str | None, lease_id: str | None) -> None:
        if rebuild_id != job.get("rebuild_id"):
            raise Conflict("rebuild_job_mismatch")
        if job.get("fingerprint") != fingerprint:
            raise Conflict("job_mismatch")
        prefix = "rebuild" if job.get("manual_rebuild") else "job"
        if job.get("lease_until", 0) <= self.clock().timestamp():
            raise Conflict(prefix + "_lease_not_active")
        if not job.get("lease_id") or lease_id != job["lease_id"]:
            raise Conflict(prefix + "_lease_mismatch")

    def _require_preferences(self, job: dict, revision: int | None) -> None:
        if revision != self.state["preferences"]["revision"] or job.get("preferences") != self.state["preferences"]:
            raise Conflict("preferences_revision_mismatch")

    def publish(self, edition: Edition) -> dict:
        with self.lock:
            day = edition.date.isoformat()
            messages = self._messages(day)
            ids = {m["id"] for m in messages}
            if not messages or fingerprint(messages) != edition.fingerprint:
                raise Conflict("source_fingerprint_changed")
            if len(edition.source_message_ids) != len(ids) or set(edition.source_message_ids) != ids:
                raise Conflict("source_message_ids_mismatch")
            if [d.isoformat() for d in edition.source_dates] != sorted({m["date"] for m in messages}):
                raise Conflict("source_dates_mismatch")
            used = {sid for section in edition.sections for sid in section.source_ids}
            if used != ids or any(len(s.source_ids) != len(set(s.source_ids)) for s in edition.sections):
                raise Conflict("section_source_ids_mismatch")
            try:
                tasks = {message["id"]: subject_task(message["subject"]) for message in messages}
            except (KeyError, TypeError, ValueError):
                raise Conflict("source_task_not_allowed") from None
            if any(len({tasks[sid] for sid in section.source_ids}) != 1 for section in edition.sections):
                raise Conflict("section_mixed_source_tasks")
            summarized = set()
            for summary in edition.learning_summaries:
                summary_ids = set(summary.source_ids)
                if len(summary_ids) != len(summary.source_ids) or not summary_ids.issubset(ids):
                    raise Conflict("learning_summary_source_ids_mismatch")
                summary_tasks = {tasks[sid] for sid in summary_ids}
                if len(summary_tasks) != 1:
                    raise Conflict("learning_summary_mixed_source_tasks")
                task = summary_tasks.pop()
                if task in summarized or self.state["preferences"]["sources"][task]["category"] != "learning":
                    raise Conflict("learning_summary_task_mismatch")
                covered = {sid for section in edition.sections if tasks[section.source_ids[0]] == task for sid in section.source_ids}
                if summary_ids != covered:
                    raise Conflict("learning_summary_source_ids_mismatch")
                summarized.add(task)
            if edition.generated_at > self.clock() + timedelta(minutes=5):
                raise Conflict("future_generated_at")
            if edition.preferences_revision != self.state["preferences"]["revision"]:
                raise Conflict("preferences_revision_mismatch")
            if edition.format != "mixed":
                raise Conflict("edition_format_mismatch")
            existing = self.state["editions"].get(day)
            job = self.state["jobs"].get(day, {})
            manual = job.get("manual_rebuild", False) and job.get("fingerprint") == edition.fingerprint
            if existing and existing["fingerprint"] == edition.fingerprint and not manual:
                if existing.get("preferences_revision") != edition.preferences_revision or existing.get("format") != edition.format:
                    raise Conflict("preferences_revision_mismatch")
                if edition.rebuild_id != existing.get("rebuild_id"):
                    raise Conflict("rebuild_job_mismatch")
                if edition.lease_id != existing.get("lease_id"):
                    raise Conflict("job_lease_mismatch")
                return {"status": "duplicate", "date": day}
            self._require_preferences(job, edition.preferences_revision)
            self._require_lease(job, edition.fingerprint, edition.rebuild_id, edition.lease_id)
            # Source identity comes from stored mail; retain each source's own
            # sequence without modifying the worker's submitted Edition object.
            edition = edition.model_copy(deep=True)
            edition.sections.sort(key=lambda section: SOURCE_ORDER[tasks[section.source_ids[0]]])
            state = deepcopy(self.state)
            if existing:
                state.setdefault("history", {}).setdefault(day, []).append(deepcopy(existing))
            state["editions"][day] = edition.model_dump(mode="json")
            state["jobs"].pop(day, None)
            self._commit(state)
            return {"status": "published", "date": day, "sections": len(edition.sections)}

    def failure(self, payload: Failure) -> dict:
        with self.lock:
            day = payload.date.isoformat()
            if fingerprint(self._messages(day)) != payload.fingerprint:
                raise Conflict("source_fingerprint_changed")
            state = deepcopy(self.state)
            prior = state["jobs"].get(day, {})
            self._require_preferences(prior, payload.preferences_revision)
            self._require_lease(prior, payload.fingerprint, payload.rebuild_id, payload.lease_id)
            attempts = max(1, prior.get("attempts", 0) if prior.get("fingerprint") == payload.fingerprint else 0)
            blocked = attempts >= 3
            state["jobs"][day] = {"fingerprint": payload.fingerprint, "attempts": attempts, "lease_until": 0, "next_attempt_at": self.clock().timestamp() + min(3600, 60 * 2 ** (attempts - 1)), "error": payload.code,
                                  "preferences": deepcopy(prior["preferences"])}
            if prior.get("manual_rebuild"):
                state["jobs"][day].update(manual_rebuild=True, rebuild_id=prior["rebuild_id"], lease_id=prior["lease_id"])
            self._commit(state)
            return {"status": "blocked" if blocked else "retry_wait", "attempts": attempts}

    def read(self, request: ReadRequest) -> dict:
        with self.lock:
            today = self.clock().astimezone(SHANGHAI).date().isoformat()
            labels = {"daily": "每日简报", "learning": "学习内容", "work": "工作日程", "full": "早报"}
            label = labels[request.view]
            # Work is opt-in. Do not even inspect its cached dates while disabled.
            if request.view == "work" and not self.state["preferences"]["work_enabled"]:
                day = today if request.date == "today" else None if request.date == "latest" else request.date
                return {"status": "disabled", "date": day, "view": request.view, "title": "",
                        "spoken_text": "工作日程尚未开启，在设置页开启后可读取。", "full_spoken_text": "",
                        "full_sources": [], "sources": [], "source_dates": [], "next_section": None,
                        "total_sections": 0, "latest_date": None, "section": request.section, "topic": request.topic}
            latest = max(self.state["editions"], default=None)
            day = today if request.date == "today" else latest if request.date == "latest" else request.date
            edition = self.state["editions"].get(day)
            result = {"status": "missing", "date": day, "view": request.view, "title": "", "spoken_text": f"{day or '目前'}的{label}尚未准备好。", "full_spoken_text": "", "full_sources": [], "next_section": None, "total_sections": 0, "source_dates": [], "sources": [], "latest_date": latest, "section": request.section, "topic": request.topic}
            if edition is None:
                return result
            messages = self._messages(day)
            try:
                view_sections = select_view(edition, messages, self.state["preferences"], request.view)
            except ValueError:
                result.update(status="unavailable", spoken_text=f"{day}的{label}暂时无法读取，请稍后重试。")
                return result
            if not view_sections:
                result.update(status="view_missing", spoken_text=f"{day}尚无可读取的{label}。")
                return result
            sections = [s for s in view_sections if request.topic == "all" or s["topic"] == request.topic]
            source_ids = {source_id for section in sections for source_id in section["source_ids"]}
            all_cached_ids = {source_id for section in edition["sections"] for source_id in section["source_ids"]}
            unfiltered_full = request.view == "full" and source_ids == all_cached_ids
            # Edition-level prose can mention hidden categories, so filtered views
            # expose only fixed labels and metadata scoped to the selected sources.
            result.update(date=edition["date"], title=edition["title"] if unfiltered_full else label,
                          total_sections=len(sections), source_dates=sorted({m["date"] for m in messages if m["id"] in source_ids}),
                          generated_at=edition["generated_at"], warnings=edition["warnings"] if unfiltered_full else [])
            if sections:
                full_sources = [{k: m[k] for k in ("id", "subject", "date", "source_url")} for m in messages if m["id"] in source_ids]
                paragraphs = [f"这期{label}日期是{edition['date']}。"]
                paragraphs.extend(f"第{index}段，共{len(sections)}段。" + section["text"] for index, section in enumerate(sections, 1))
                paragraphs.append(f"本次{label}{'' if request.view == 'learning' else '内容'}已播完。")
                result.update(full_spoken_text="\n\n".join(paragraphs), full_sources=full_sources)
            if not sections:
                result.update(status="topic_missing", spoken_text=f"{day}的{label}中没有这个主题。")
            elif request.section > len(sections):
                result.update(status="finished", spoken_text=f"{day}的{label}已经播完。")
            else:
                section = sections[request.section - 1]
                source_ids = set(section["source_ids"])
                sources = [{k: m[k] for k in ("id", "subject", "date", "source_url")} for m in messages if m["id"] in source_ids]
                prefix = f"这期{label}日期是{day}。第{request.section}段，共{len(sections)}段。"
                result.update(status="ready", spoken_text=prefix + section["text"], next_section=request.section + 1 if request.section < len(sections) else None, sources=sources)
            return result
