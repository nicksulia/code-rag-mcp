# Specification: Embedding Model Lifecycle

## Status: ACTIVE
## Domain: Model Runtime & Lifecycle

---

## 1. Overview
Defines how the code RAG engine owns and schedules the local embedding model's residency in memory, so the model occupies VRAM/RAM only while embedding or search work is actually running, and only one engine instance controls that lifecycle at a time.

---

## 2. Requirements

### 2.1 Single-Instance Ownership Of The Embedding Runtime
- The system permits only one engine instance to own the embedding model lifecycle for a given data directory.
- A second instance attempting to start against the same data directory fails fast with an actionable message identifying the owning process, and does not load the embedding model.
- The ownership claim is released on normal exit and is reclaimable after an abnormal termination: a stale claim whose owning process no longer exists does not block startup.

### 2.2 On-Demand Model Loading
- The embedding model is never loaded into memory as a side effect of process startup, service construction, configuration inspection, repository registration, or capability probing.
- The model is loaded only when an embedding or search operation begins (e.g. the first search request after startup).

### 2.3 Release After Operations Complete
- The embedding model is released from the model runtime after the last in-flight embedding or search operation completes and a configured idle grace period elapses with no new operation.
- Release occurs for successful, failed, and cancelled operations alike (e.g. an indexing run that aborts with an error still releases the model once no other operation is in flight).
- Consecutive requests arriving within the idle grace period keep the model resident, and it is unloaded only after the final request plus the grace period.

### 2.4 Shared Activation Across Concurrent Operations
- Concurrent embedding and search operations within the single instance are reference-counted so that overlapping operations trigger exactly one load and exactly one release.
- Activation and release are thread-safe.
- A new operation starting while a release is pending cancels that release and reuses the resident model rather than triggering a reload.

### 2.5 Configurable Lifecycle Policy
- The residency policy is configurable, including the idle grace period and an always-resident mode that keeps the model loaded for the process lifetime.
- Configuration is settable via environment variable and CLI option, with on-demand release as the default.
- A manual unload request releases the model when no operation is in flight; if requested while an operation is in progress, the in-flight run is not interrupted and the response reports that the model is busy.

### 2.6 Lifecycle Observability
- Model load and release transitions, including the reason for the transition and the duration of the load, are recorded in the logging output.
- The status interface exposes the current residency state (loaded or released) and the active operation count.

### 2.7 Degraded Runtime Handling
- When the model runtime is unavailable or the configured model cannot be loaded, the system surfaces a clear error for the affected operation and falls back to the offline embedding path where a fallback is defined.
- The instance always returns to a released state with an active operation count of zero rather than leaving a partially-loaded model or a stuck reference count.
