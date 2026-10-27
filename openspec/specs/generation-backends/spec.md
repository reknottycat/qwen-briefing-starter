# generation-backends Specification

## Purpose

Allow an operator to generate cached briefings through their existing Codex configuration or a lightweight Pi cloud-model client without changing reading behavior.

## Requirements

### Requirement: Fixed client selection
The worker SHALL support Codex and Pi through configuration, preserving Codex compatibility. Both MUST use the existing source attribution, raw-text preservation, draft validation and publication contract. Selecting a client MUST NOT require users or agents to edit application code.

#### Scenario: Alternative cloud client
- **WHEN** an operator configures Pi with their chosen cloud provider and model
- **THEN** generation uses that client and publishes only a validated draft, without requiring DGX or GPU

### Requirement: Bounded untrusted generation
Generation SHALL have bounded input, output and execution time. Source messages MUST NOT enable tools or inherit application role credentials. Failure MUST produce a bounded non-secret error and leave the previous published edition intact.

#### Scenario: Invalid model output
- **WHEN** the client returns malformed output, excessive content or invalid attribution
- **THEN** the worker records a generation failure and does not publish that draft

#### Scenario: Raw-only edition
- **WHEN** every selected source uses raw mode
- **THEN** no model client is invoked and preserved text follows existing read-view behavior

### Requirement: Private Pi web configuration
An authorized operator SHALL configure Pi through the existing management page after Docker starts. Provider and model choices MUST be derived from the pinned Pi catalog, including API-key-based MiniMax, Moonshot/Kimi and DeepSeek choices when present. The page MUST also support explicit third-party compatible endpoints. The worker MUST use the saved configuration on subsequent generation without requiring a service restart.

#### Scenario: First model setup
- **WHEN** a fresh Docker installation has no model credentials
- **THEN** management remains available, the worker waits without claiming jobs, and the operator can select a supported provider/model and save a key

#### Scenario: Credential privacy
- **WHEN** the operator saves or reads model configuration
- **THEN** the key is held in restricted server-side storage, never returned in responses or logs, and not stored in browser local or session storage

#### Scenario: Changing credential destination
- **WHEN** the provider, endpoint or protocol changes
- **THEN** the operator must supply a new key instead of silently sending the retained credential to a different destination

#### Scenario: Concurrent edit and custom endpoint
- **WHEN** an operator submits a stale revision or an invalid custom endpoint
- **THEN** the request is rejected without overwriting the current configuration or echoing the submitted key
