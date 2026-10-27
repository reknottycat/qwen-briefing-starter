# Spec Delta

## Purpose

Run briefing ingestion, cached API reads and advance generation on a small Linux server using a reproducible container deployment and operator-owned credentials.

## ADDED Requirements

### Requirement: Separate container responsibilities
The deployment SHALL run one API process with persistent state and a separate generation worker. External reads MUST use the operator's HTTPS endpoint; the sample host API port SHALL bind to loopback. Containers MUST NOT require GPU.

#### Scenario: Cached request
- **WHEN** a client reads a published edition
- **THEN** the API serves cached content without waiting for a model call

### Requirement: Separate web credential storage
The management API MAY accept model credentials but SHALL NOT return them to clients or other HTTP roles. A dedicated private directory SHALL be writable by the API and mounted read-only for the worker. Ordinary state backups and public build inputs MUST exclude it.

#### Scenario: Web model credentials
- **WHEN** the operator saves a Pi key after a fresh Docker startup
- **THEN** only the authenticated HTTPS management operation writes it to the dedicated private settings directory, the worker reads it on the next generation, and no API response contains it

### Requirement: Reproducible container acceptance
An independent smoke check SHALL build and start the container deployment with synthetic data, observe authentication and generation-to-read behavior, and clean up its own resources. Mock provider acceptance MUST be labelled separately from real provider and device acceptance.

#### Scenario: Isolated smoke run
- **WHEN** the smoke check runs with a synthetic provider
- **THEN** it verifies denied anonymous reads, successful input publication and authenticated content readback without using real messages or paid credentials
