# Proposal

## Why

The public starter still assumes a preselected source list, an authenticated Codex worker, and manual deployment. A new user needs to select existing daily tasks, approve categories and a cloud destination, then connect their own Qwen device without requiring DGX or exposing credentials in chat.

## What Changes

- Add a resumable setup CLI and an Agent Skill shared by Codex and OpenCode, with task selection before server confirmation and Qwen handoff.
- Exclude monitoring, non-daily, inactive and unverified tasks from initial setup; preserve existing scheduler jobs.
- Load validated private source policy and initial preferences for a fresh installation.
- Add a small Pi cloud-model worker adapter while retaining Codex and existing output validation. Configuration Skills remain usable from Codex or OpenCode.
- Add a Docker API/worker deployment and an independent container smoke check.
- Document module boundaries, user-managed authentication, and the official Qwen HTTP plugin route.

## Capabilities

### New Capabilities

- `guided-setup`: Task discovery, confirmation order, private configuration export and Qwen handoff.
- `generation-backends`: Configurable cloud and CLI generation using the existing publishing contract.
- `container-deployment`: Isolated API and worker services with persistent state and private configuration.

### Modified Capabilities

None. Existing portable-release credential and installed-package requirements remain applicable.

## Impact

Changes affect setup skills, worker configuration, fresh-store initialization, deployment templates, documentation, tests and CI. Existing private deployments are not changed; source-list migrations for populated stores remain explicit operations outside this fresh-install flow.
