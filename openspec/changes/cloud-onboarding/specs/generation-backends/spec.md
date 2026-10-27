# Spec Delta

## Purpose

Allow an operator to generate cached briefings through their existing Codex configuration or a lightweight Pi cloud-model client without changing reading behavior.

## ADDED Requirements

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
