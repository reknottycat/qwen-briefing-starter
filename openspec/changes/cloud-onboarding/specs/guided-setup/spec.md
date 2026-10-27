# Spec Delta

## Purpose

Guide a new operator from existing scheduled tasks to a private briefing installation, with clear user confirmation and no credential collection in conversation.

## ADDED Requirements

### Requirement: Evidence based task selection
Setup SHALL discover existing tasks read-only and include only active daily non-monitoring tasks with verified classification. The user MUST confirm selected task identities, categories and narration preferences before selecting a server. Unknown, weekly, inactive and monitoring tasks SHALL be excluded from the initial selection.

#### Scenario: Uncertain task purpose
- **WHEN** a task has a daily schedule but its monitoring status is unknown
- **THEN** setup requires evidence-backed review before including it and does not change the scheduler

#### Scenario: Confirmed custom sources
- **WHEN** the user confirms eligible tasks and news, learning or work categories
- **THEN** exported source policy and preferences use those exact names and order, with work disabled unless explicitly enabled

### Requirement: Ordered bound confirmations
Setup SHALL bind task and subsequent server confirmations to the reviewed configuration. It MUST reject stale confirmations and never overwrite previous setup files. Qwen handoff SHALL be generated only after both confirmations.

#### Scenario: Selection changes
- **WHEN** task selection changes after confirmation
- **THEN** the old confirmation cannot authorize server export or Qwen handoff

#### Scenario: Missing server confirmation
- **WHEN** only task selection is confirmed
- **THEN** configuration export and device handoff remain unavailable

### Requirement: Private fresh installation
The application SHALL accept validated operator source policy and initial preferences from private runtime files. Initial preferences SHALL apply only to new state; existing effective preferences MUST be preserved. Incompatible source identities MUST fail before replacing state.

#### Scenario: Restart after user preference changes
- **WHEN** a running installation restarts with the original initial-preferences file
- **THEN** saved effective preferences remain unchanged

### Requirement: User managed platform login
The setup Skill SHALL explain module boundaries and require users to configure SSH and model credentials locally and log into Qwen in their browser. It SHALL use the official HTTP plugin and Speak route without requiring a device SDK, and distinguish backend tests, tool calls, review status and device playback.

#### Scenario: Qwen handoff
- **WHEN** confirmed server preparation reaches device connection
- **THEN** the instructions request the user's browser login and actual platform plugin/tool identifiers, not their password, cookies or an invented gateway credential
