## Purpose

Defines how the code RAG engine owns and schedules the local embedding model's residency in memory, so the model occupies VRAM/RAM only while embedding or search work is actually running, and only one engine instance controls that lifecycle at a time.

## ADDED Requirements

### Requirement: Single-instance ownership of the embedding runtime
The system SHALL permit only one engine instance to own the embedding model lifecycle for a given data directory. A second instance attempting to start against the same data directory SHALL fail fast with an actionable message identifying the owning process, and SHALL NOT load the embedding model. The ownership claim SHALL be released on normal exit and SHALL be reclaimable after an abnormal termination (a stale claim whose owning process no longer exists MUST NOT block startup).

#### Scenario: Second instance is rejected
- **WHEN** an engine instance is already running against data directory `D` and a second instance is started against `D`
- **THEN** the second instance exits with a non-zero status and a message naming the owning process id, and no additional embedding model is loaded into memory

#### Scenario: Stale claim is reclaimed
- **WHEN** an engine instance is started against a data directory whose ownership claim was left behind by a process that is no longer running
- **THEN** the instance takes ownership and starts normally

#### Scenario: Ownership released on exit
- **WHEN** a running engine instance shuts down normally
- **THEN** the ownership claim is released and a subsequent instance starts without error

### Requirement: Embedding model is loaded on demand, not at startup
The system SHALL NOT cause the embedding model to be loaded into memory as a side effect of process startup, service construction, configuration inspection, repository registration, or capability probing. The model SHALL be loaded only when an embedding or search operation begins.

#### Scenario: Startup leaves memory untouched
- **WHEN** the engine process starts and reaches its ready state without any search or index request
- **THEN** the embedding model is not resident in the model runtime

#### Scenario: First search loads the model
- **WHEN** the first search request arrives after startup
- **THEN** the embedding model is loaded, the query is embedded, and results are returned

### Requirement: Embedding model is released after operations complete
The system SHALL release the embedding model from the model runtime after the last in-flight embedding or search operation completes and a configured idle grace period elapses with no new operation. Release SHALL occur for successful, failed, and cancelled operations alike.

#### Scenario: Release after a search burst
- **WHEN** a search completes and no further embedding or search operation starts within the idle grace period
- **THEN** the embedding model is unloaded from the model runtime

#### Scenario: Release after a failed operation
- **WHEN** an indexing run aborts with an error after the model was loaded
- **THEN** the model is still released once no other operation is in flight

#### Scenario: Grace period suppresses thrashing
- **WHEN** consecutive search requests arrive with gaps shorter than the idle grace period
- **THEN** the model remains loaded across those requests and is unloaded only after the final request plus the grace period

### Requirement: Concurrent operations share one activation
The system SHALL reference-count concurrent embedding and search operations within the single instance so that overlapping operations trigger exactly one load and exactly one release. Activation and release SHALL be thread-safe, and an operation starting while a release is pending SHALL cancel that release and reuse the resident model.

#### Scenario: Overlapping searches load once
- **WHEN** three search requests overlap in time
- **THEN** the model is loaded once, all three requests are served, and the model is released once after the last one finishes

#### Scenario: Late request cancels pending release
- **WHEN** a new search starts during the idle grace period following a previous operation
- **THEN** the pending release is cancelled and the new search proceeds without a reload

### Requirement: Lifecycle policy is configurable
The system SHALL expose configuration for the residency policy, including the idle grace period and an always-resident mode that keeps the model loaded for the process lifetime. Configuration SHALL be settable via environment variable and CLI option, with on-demand release as the default. An explicit manual unload request SHALL release the model when no operation is in flight, and SHALL report that the model is busy rather than interrupting an in-flight operation.

#### Scenario: Always-resident mode opts out
- **WHEN** the engine is configured for always-resident mode and a search completes
- **THEN** the model remains loaded after the idle grace period

#### Scenario: Manual unload while idle
- **WHEN** a manual unload is requested and no embedding or search operation is in flight
- **THEN** the model is released and the response reports success

#### Scenario: Manual unload while busy
- **WHEN** a manual unload is requested while an indexing run is in progress
- **THEN** the in-flight run is not interrupted and the response reports that the model is busy

### Requirement: Lifecycle transitions are observable
The system SHALL report model load and release transitions, including the reason for the transition and the duration of the load, through its logging output, and SHALL expose the current residency state (loaded or released, active operation count) through its status interface.

#### Scenario: Status reflects residency
- **WHEN** the status interface is queried while no operation is in flight and the model has been released
- **THEN** the reported residency state is "released" with an active operation count of zero

#### Scenario: Load is logged
- **WHEN** the model is loaded for an incoming search
- **THEN** a log entry records the load, its trigger, and the elapsed load time

### Requirement: Degraded runtime does not break operations
When the model runtime is unavailable or the configured model cannot be loaded, the system SHALL surface a clear error for the affected operation and SHALL fall back to the offline embedding path where a fallback is defined, rather than leaving a partially-loaded model or a stuck reference count.

#### Scenario: Runtime offline during search
- **WHEN** a search begins and the model runtime is unreachable
- **THEN** the search reports the runtime error or serves results via the defined offline fallback, and the instance returns to a released state with an active operation count of zero
