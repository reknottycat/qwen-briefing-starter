# Spec Delta

## Purpose

Run briefing ingestion, cached API reads and advance generation on a small Linux server using a reproducible container deployment and operator-owned credentials.

## ADDED Requirements

### Requirement: Separate container responsibilities
The deployment SHALL run one API process with persistent state and a separate generation worker. External reads MUST use the operator's HTTPS endpoint; the sample host API port SHALL bind to loopback. Containers MUST NOT require GPU or expose model credentials to the API.

#### Scenario: Cached request
- **WHEN** a client reads a published edition
- **THEN** the API serves cached content without waiting for a model call

### Requirement: Reproducible container acceptance
An independent smoke check SHALL build and start the container deployment with synthetic data, observe authentication and generation-to-read behavior, and clean up its own resources. Mock provider acceptance MUST be labelled separately from real provider and device acceptance.

#### Scenario: Isolated smoke run
- **WHEN** the smoke check runs with a synthetic provider
- **THEN** it verifies denied anonymous reads, successful input publication and authenticated content readback without using real messages or paid credentials
