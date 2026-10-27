## Context

The core already separates ingestion, background preparation, and cached reads.
This release keeps that behavior and removes environment-specific bindings.

## Goals / Non-Goals

Make another operator's installation explicit, distributable, and reviewable.
Do not add hosting, account registration, device playback guarantees, or a new
generation framework to the application.

## Decisions

- Use an independent Git history and generic source examples; exclude runtime data.
- Keep the four credential roles and require an explicit HTTPS endpoint and Gmail
  account. Preserve existing credentials when regenerating environment files.
- Use the existing Codex CLI through PATH or an explicit executable path.
- Use pyproject.toml as the package version and dependency source. Build a wheel
  for the API and a source distribution for templates, docs, and skills.
- Verify the installed wheel outside the source import path. Keep Linux CI and
  local unit evidence separate from real-mail and native-device acceptance.

## Risks / Trade-offs

The service remains single-process JSON storage. Existing source registries need
an explicit migration when names change. Qwen connection deadlines and repeated
requests may fail independently of API tests; these limits remain documented.

## Migration Plan

Use a fresh deployment with the generic policy. Do not replace the policy of an
existing private deployment without a reviewed state migration and backup.
