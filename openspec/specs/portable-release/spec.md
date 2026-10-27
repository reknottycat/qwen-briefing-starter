# portable-release Specification

## Purpose
Provide a self-hosted briefing distribution that another operator can configure
without inheriting private deployment bindings, credentials, content, or history.

## Requirements

### Requirement: Explicit portable configuration
The application SHALL require the operator's API URL and Gmail account instead of using a personal deployment default. Codex SHALL be resolved from explicit configuration or PATH.

#### Scenario: Missing required configuration
- **WHEN** an operator invokes configuration without the API URL or the collector without its account property
- **THEN** the operation fails with a specific configuration error before making a request

#### Scenario: Configuration is regenerated
- **WHEN** an operator regenerates configuration in an existing runtime directory
- **THEN** existing role credentials are preserved and credentials are not printed

### Requirement: Installable distribution
The release SHALL include the API's source policy and management assets in its wheel and deployment instructions in its source distribution.

#### Scenario: Fresh wheel installation
- **WHEN** the wheel is installed into a clean environment outside the source checkout
- **THEN** health and authenticated preference reads work, anonymous reads fail, work is disabled initially, and management assets are served

### Requirement: Private runtime isolation
The repository SHALL omit personal emails, generated briefings, credentials, runtime receipts, and private deployment history.

#### Scenario: Release review
- **WHEN** the repository and distribution files are reviewed
- **THEN** only generic examples and implementation files are present, and documented limitations do not claim unverified native audio or platform approval
