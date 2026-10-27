# Design

## Context

The API, state store, Gmail collector, cache views and output validation already work independently from GPU hardware. The missing parts are fresh-user setup, configurable private source names, a generic generation client and container packaging. The existing public release uses Codex and one JSON state writer.

## Goals / Non-Goals

**Goals:** Keep one small pipeline; offer Codex and Pi; guide existing task selection, server confirmation and Qwen browser login in order; produce private configuration without editing packaged code; demonstrate container behavior with synthetic inputs.

**Non-Goals:** Create a scheduler, migrate populated deployments automatically, bundle or vendor an agent framework, build an audio player, add OpenCode as a server backend, or claim unverified voice permissions and device playback.

## Decisions

- Reuse the worker's validation and publication path. Pi is the only new backend; Codex remains supported for existing model gateway configurations. Skills may be invoked from any compatible host, including OpenCode.
- Reuse the existing source-policy v1 schema and load a private policy path at process startup. A separate initial-preferences file seeds only new state; it never replaces saved preferences. Source-list changes in an existing deployment require an explicit separate migration.
- A standard-library CLI writes immutable setup stages. Digests bind confirmation to the reviewed proposal. The Skill obtains human confirmation; command flags record that decision, not independent proof of consent.
- Discovery reads supplied Codex automation files or a normalized export from the user's existing scheduler. Unknown monitoring classification requires review; there is no assumption that every Agent has a scheduled-task connector.
- One Docker image runs API and worker as separate services. Use the official Pi package, read-only configuration and scoped credential mounts, persistent API storage and loopback host exposure behind existing TLS.
- Qwen connection uses the official cloud HTTP plugin and native Speak. Users log into their own browser session; no SDK, password or Cookie is required by this integration.

## Risks / Trade-offs

- Pi's cloud provider compatibility needs a real operator configuration test; synthetic client and container tests only establish the integration protocol.
- Native Qwen audio length, role-specific voices and permissions require platform and device evidence. Do not infer them from text dialogue or web TTS.
- JSON state requires one API process; initial setup deliberately refuses automatic source migrations.
