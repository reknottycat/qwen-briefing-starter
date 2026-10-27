# Qwen Briefing Starter

Reuse Gmail, FastAPI, Codex or Pi CLI generation, and Qwen's HTTP tool and native Speak capability. Keep cloud ingestion, advance generation and cached reads separate. Codex/OpenCode may host the setup Skill; server generation supports Codex or Pi only. Do not add another agent framework, audio player or Windows-dependent scheduler. Cloud model calls do not require DGX or a GPU.

Never commit private messages, generated briefings, credentials, preferences or runtime receipts. Keep runtime files under ignored `runtime/`; use synthetic fixtures in tests. Read relevant [operating lessons](docs/lessons-learned.md) before related changes.

Use the existing OpenSpec workflow for behavior changes. Source identity and order belong in the private policy selected by `BRIEFING_SOURCE_POLICY_PATH`, with `briefing/source_policy.json` as the example fallback. Initial preferences are for fresh state only; effective preferences belong to cloud state and must be changed through the management API. Coordinate policy changes across API, worker and collector, with a tested migration for existing state.

For first installation, use `setup-qwen-briefing` and [the setup flow](docs/setup.md): discover existing active daily non-monitoring tasks, obtain confirmation of the exact source proposal, then confirm the server proposal, and only then connect Qwen. Confirmation flags record prior user approval; they do not grant it. Keep credentials in user-managed local files and preserve existing schedules. Maintenance uses `add-to-briefing` without repeating valid setup confirmations.

Run `python -m pytest` and `node --test tests/test_collector.cjs` before release. Follow [the acceptance contract](docs/acceptance.md); distinguish source tests, deployed HTTP checks, platform tool calls and full device playback. Preserve the 3-second HTTP acceptance budget. Verify package changes from an installed wheel outside the repository.

Keep one API process and one Uvicorn worker while state uses JSON. Version only in `pyproject.toml`. Do not claim native Codex hooks or CI passed without observed evidence. Update only affected docs and tests; keep private evidence out of public changes.
