# Design

## Context

See `proposal.md` for motivation and the three delta specs for observable contracts.

- The prerequisite `fix-repository-sync-failures` is implemented in the current worktree: all 26 tasks are checked off, and focused Git/service/API/CLI/update coverage passes. Its checked Git execution, safe domain errors, saved-configuration context, pre-index state preservation, and explicit `local_only` indexing are the baseline to extend, not work to repeat.
- `_run_git` centralizes checked argument-list execution, subprocess-local environment settings, disabled prompts/input, and 120/15-second deadlines. It uses `subprocess.run(..., start_new_session=True)` but does not explicitly terminate the operation's process group on timeout. Managed identity context, HTTP transport policy, and descendant cleanup remain new work.
- The current catalog has repositories, hashes, groups, and dependencies, but no key model. `save_repo` uses `INSERT OR REPLACE`; adding an association table with foreign keys without changing that statement would erase associations on ordinary status saves.
- `get_repo`/`list_repos` map positional repository rows into `Repository`. Transfer instead projects the exact five version 1 configuration fields, which must remain unchanged.
- Add/Edit reaches Git through service methods. Registration saves before automatic sync, and `update_repo` now saves desired configuration before branch/remote preparation; Git failures retain it with `config_saved=True`. Each `save_repo` opens and commits its own connection, so atomic configuration/association writes require a new connection-aware write path. `auto_sync=False` skips indexing, not existing branch/remote preparation.
- Existing Git subprocesses inherit the server environment. There is no SSH custody, agent management, or REST authentication. `/mcp` has optional bearer authentication and hostname-only origin filtering; REST uses wildcard CORS.
- HTTP is threaded. Static serving currently opens a requested path without proving web-root containment. Source reads prove repository-root containment but do not exclude private files; scanning hashes files before any key-specific check.
- The UI has three navigation tabs and an implemented repository-operation flow: `repositoryRequest`, `RepositoryRequestError`, shared in-flight controls/results, and `refreshRepositoryOperation` with strict repository/graph refresh. `repositoryOperations.savedAddId` turns a persisted-but-failed Add into a PUT-based **Save & Retry**. Key management should remain separate from the Repositories hub while selectors extend this flow.
- The target is the repository resolved by OpenSpec, with POSIX/OpenSSH available on the user's development environment. Tests use standard-library `unittest`, temporary catalogs, subprocess mocks, and in-memory handlers; no frontend framework is checked in.

## Goals / Non-Goals

**Goals:**

- A profile reference is machine-local metadata, never a private-key vault.
- Assignment changes are explicit and atomic with configuration; runtime saves cannot accidentally clear them.
- Restrict authentication at the actual SSH target, not merely add another identity to the user's existing identity list.
- Share authentication and safe errors across every Git entry point, with per-operation snapshots and subprocess-local state.
- Protect both provisioning and credential use before exposing the feature through a browser.

**Non-Goals:**

- Upload/paste, key generation, private-file copying, in-app unlock, agent mutation, secret encryption infrastructure, or multi-user authorization.
- Automatic host-to-key discovery, HTTPS-token management, arbitrary custom SSH launchers in managed mode, or changing proxy-hop credentials.
- Remote HTTP SSH use, Windows/legacy-PEM profile support, changing version 1 project documents, or transactional rollback of Git/index side effects.
- Implementing the prerequisite as part of proposal generation or changing its artifacts without a separate request.

## Decisions

### D1: Add profile and association tables, not transfer fields

Add `ssh_key_profiles(key_id, name, private_key_path, fingerprint, algorithm, created_at)` and `repo_ssh_keys(repo_id PRIMARY KEY, key_id)` to the existing catalog. Generate opaque key IDs; canonical path and fingerprint are individually unique. Associations reference existing repositories/profiles, cascade on repository deletion, and restrict profile deletion.

Enable foreign-key enforcement on connections touching these tables. Replace repository `INSERT OR REPLACE` with explicit `ON CONFLICT(repo_id) DO UPDATE` preserving the intended repository metadata update without delete/reinsert. Ordinary `save_repo` never writes `repo_ssh_keys`. Explicit assignment changes alone update that table. Keep repository deletion and relation cleanup atomic with association deletion.

Introduce a connection-aware repository-write path used by both ordinary saves and explicit configuration/association transactions. The existing public `save_repo` remains a connection-owning wrapper for runtime callers; registration/update use one connection and commit metadata plus an explicitly changed binding together. Do not compose the existing separately committed `save_repo` and a binding save and call that atomic. Reuse the transfer code's connection/transaction patterns without changing its insert-only behavior.

Add nullable `ssh_key_id` to the public Repository projection, populated by an explicit left join or separate metadata lookup rather than changing positional row interpretation accidentally. Project only the ID on ordinary lists/MCP results. The protected local profile API separately returns the spec's exact metadata and sorted association IDs; it does not expose `created_at`.

Transfer continues using its existing explicit five-field projection, and its insert-only import never touches associations. New imported rows are unassigned; unchanged entries preserve bindings. Key/association edits therefore do not affect exported bytes.

**Alternative rejected:** Putting key fields in `Repository.to_dict()` and exporting it leaks machine-local authentication. A column updated by every status save can overwrite a recently changed association from a stale Repository object.

### D2: Validate references locally and never own key material

Use a focused standard-library SSH support module beside `repo_manager.py` for filesystem checks, public metadata extraction, safe errors, and the internal SSH launcher. It must be runnable by absolute script path without importing code from the cloned repository's working directory.

Accept a label of 1-80 characters and an absolute or server-account `~/` path up to 4,096 UTF-8 bytes. Reject controls and relative paths. Resolve symlinks to a canonical regular file; validate effective-account ownership, readability, and no group/other mode bits. Read only the bounded format header in application validation and require the OpenSSH private-key marker, not a `.pub` file or arbitrary text.

Use checked `ssh-keygen -l -E sha256 -f <path>` with disabled stdin and a 15-second deadline to extract only fingerprint/algorithm. OpenSSH-format public metadata is available even for encrypted keys. Parse exactly one supported result and discard comments/raw output; do not attempt decryption or ask for a passphrase. If the installed tool cannot inspect the format noninteractively, return an explicit unsupported/validation error, not guessed metadata. Never chmod, copy, generate, or upload the user's key.

Before registration, compare the canonical file and `(device, inode)` identity to currently indexed source paths. If already indexed, reject with instructions to remove the source/index entry first; do not pretend registration retroactively removed existing indexed secrets. Before every managed remote use, revalidate file security and the fingerprint against the profile snapshot. Changed keys require a new profile and explicit reassignment.

Profiles can be renamed but not silently redirected to a different path/fingerprint. Deletion is a catalog operation with an in-use conflict; it never unlinks private/public files or calls `ssh-add`.

**Alternative rejected:** Uploading adds custody, passphrase, rotation, and deletion obligations. Inspecting a public sidecar alone can display a fingerprint unrelated to the private file; silently accepting failed inspection hides a broken profile.

### D3: Omission, null, and an ID have different meanings

At service/manager boundaries represent the assignment update as an explicit optional change object, for example a typed `{"ssh_key_id": <string-or-null>}` or no change, rather than defaulting omission and clearing to the same `None`. No unchecked casts or magic public sentinel strings are needed.

On add, omitted/null means unassigned. On update, omission preserves, null clears, and a string binds a validated existing profile. Validate the resulting source/remote and profile ID before writing configuration or invoking Git. Reuse credential-free SSH/scp address validation; reject binding to local/HTTPS/git-protocol sources. Switching a bound repository to HTTPS requires explicit null in the same update.

Extend the implemented save-before-Git ordering: persist repository configuration and binding changes in the D1 transaction before any clone/branch/remote work, retaining the prerequisite's safe error boundaries. A later Git failure retains saved desired configuration/binding and returns `config_saved=True` through the existing safe operation contract. Invalid key selection leaves both unchanged. Preserve `auto_sync=False` update semantics; it is not equivalent to sync's `local_only=True` and must not skip identity selection for branch/remote preparation.

Capture the association/profile once at the beginning of a repository operation, after an explicit assignment transaction commits, and carry that context through all its Git phases. An add's initial clone and an update's branch/remote preparation plus subsequent automatic sync share the same context; the current separate manager update and service sync calls must not independently reload the assignment. Standalone sync captures its own context. Recheck its file before each remote use, but do not swap identities mid-operation if the UI edits the association. Later operations observe later assignments. Runtime status saves do not write bindings.

CLI add/update add `--ssh-key <id>`; update adds mutually exclusive `--system-ssh`. MCP update adds nullable `ssh_key_id`, while key provisioning stays UI/REST-only. Existing service callers omitting the new option retain behavior.

**Alternative rejected:** Saving an association after `add_repository(auto_sync=True)` uses the wrong key on the first clone. A default nullable parameter that cannot distinguish omission from clearing silently discards existing bindings.

### D4: Build a target-scoped OpenSSH configuration; `-i` alone is insufficient

For unassigned trusted/local operations, leave existing authentication resolution intact, subject to the prerequisite's noninteractive/deadline controls. Do not discover profiles, start agents, or add keys. Explicit `local_only` indexing skips profile availability validation and remote preparation entirely.

For an assigned operation, extend the existing `_run_git` boundary with explicit operation context and an invocation-scoped `GIT_SSH_COMMAND` selecting the internal launcher and `GIT_SSH_VARIANT=ssh`. Preserve checked outcomes, disabled prompts/input, safe classification, and the existing phase deadlines. Quote executable/metadata paths safely for Git's command string; the launcher executes subprocess argument arrays, never evaluates constructed source text. Reject custom `GIT_SSH`/`GIT_SSH_COMMAND`, non-OpenSSH variants, or custom `core.sshCommand` in managed mode with `ssh_override_conflict` rather than silently dropping them. System SSH mode retains them.

The launcher receives Git's actual SSH arguments, so URL rewrites, user/port options, and destination aliases are resolved at the real invocation boundary. Use `ssh -G` with those arguments and a 15-second inspection deadline to derive effective target settings. Materialize a private temporary configuration with a target-only stanza containing the resolved non-identity options, plus exactly the chosen IdentityFile, `IdentitiesOnly yes`, noninteractive public-key-only authentication, no unrelated certificates, no agent forwarding to the target, and the inherited agent socket or no agent.

Do not include ordinary user/system configuration for that target: additional `IdentityFile` settings accumulate and defeat selection even with `IdentitiesOnly=yes`. For other hosts used by ProxyJump, include the ordinary user/system configuration in non-target stanzas so preconfigured proxy-hop identities/routes remain intact. Target identity selection does not replace hop authentication. Preserve known-host paths, aliases, user, port, proxy routing, and security algorithms; never turn off host-key verification or accept unknown hosts automatically.

Run a second local `ssh -G` against the generated configuration to assert the exact target identity/authentication invariants and preserved destination properties. Reject unsupported options, failed serialization, or mismatched effective settings with `ssh_config_unsupported`; do not silently omit configuration. Tests must exercise the effective configuration, not just the presence of a `-i` argument.

Own temporary configuration files under a unique per-operation mode-0700 directory, with mode-0600 files containing paths/configuration but no private-key bytes. The parent checked-runner context owns cleanup. Extend the runner's process lifecycle to terminate and reap that operation's process group on timeout/cancellation, including Git/launcher/SSH descendants, then clean up. The current `start_new_session=True` is a prerequisite for scoped ownership, not proof that `subprocess.run` already cleans up descendants. Adapt existing subprocess mocks if the execution primitive changes, retaining assertions for the exact deadlines/environment/error contract, and add real synthetic descendant-lifecycle tests.

Assigned Git operations allow only the SSH network transport so an `insteadOf` rewrite cannot silently switch to another credential mechanism. An encrypted identity without its matching unlocked agent key fails in BatchMode and receives safe external-unlock guidance; there is no in-app passphrase path or fallback.

**Alternative rejected:** `ssh -i key -o IdentitiesOnly=yes` still inherits additional configured IdentityFile entries. `-F /dev/null` alone isolates keys but breaks aliases/proxies/known-host configuration. Global environment overrides can cross-contaminate threaded repository operations.

### D5: Establish a trusted-local HTTP boundary, without a new auth platform

Classify the actual listener address using loopback address semantics, not just a string comparison. Validate the proposed/resolved binding and catalog profile presence before activating the server socket; wildcard/non-loopback bindings with any profile fail startup. Recheck the actual bound address before activation to avoid DNS resolution assumptions.

Set a service operation policy for HTTP that denies SSH network use on non-loopback listeners even with no profiles. Profile endpoints/assignment requests are forbidden there. Enforce the policy at the common Git runner as well as adapters, using a subprocess-local Git SSH-protocol denial so effective URL rewrites cannot bypass it. Do not let an HTTP MCP call regain trusted-direct privileges by using the same service. Trusted standalone CLI/stdio instances keep local privileges.

REST policy errors return HTTP 403. An allowed HTTP MCP request whose tool operation violates this policy retains the existing HTTP 200 JSON-RPC `isError` framing with a safe forbidden-operation code; do not contradict the transport's request-framing contract. Host/Origin rejections still occur before MCP dispatch with their existing HTTP denial behavior.

For loopback listeners, add one pre-dispatch guard used by every HTTP method and every route, including static files and OPTIONS. Require exactly one valid Host with an accepted loopback name/address and actual port. Validate Origin as an exact same-origin tuple; reject malformed/null/multiple origins, userinfo/paths, and cross-site/same-site Fetch Metadata. Absence of Origin remains valid for trusted local non-browser clients with a proper Host. Determine the actual port from server state, including ephemeral port bindings; test fixtures must provide realistic server/Host context.

Remove wildcard CORS on loopback REST responses and do not authorize hostile preflights. Same-origin UI needs no new bearer token storage/session. Preserve configured `/mcp` bearer checks independently. Non-loopback permitted HTTPS/local routes keep legacy REST behavior; token presence never enables SSH use or profile provisioning there.

Resolve static targets under the web root before opening, decode traversal consistently, reject root escapes/symlink escapes, and use safe not-found behavior. This applies on every listener, since a private file must not be reachable through a legacy static route.

This is a trusted local-machine model, not isolation from other local users/processes. Shared-host or direct remote managed-SSH deployment requires a separate authenticated architecture. A localhost-bound SSH tunnel can preserve the boundary when the client uses the matching localhost host/port; reverse-proxy headers are not trusted as an escape hatch.

**Alternative rejected:** Securing only key CRUD leaves add/edit/sync and HTTP MCP able to use credentials. Wildcard CORS removal alone does not stop cross-origin mutations or DNS rebinding. Extending MCP_AUTH_TOKEN's meaning silently would break the dashboard and still need a browser authentication design.

### D6: Protect key files before source ingestion or file return

Share a protected-file predicate across registration checks, repository scanning, source reads, and static reads. Use known canonical paths/file identities and a bounded header check for recognized private-key markers; do not read full private material to classify it. Exclude protected files before hashing/chunking/embedding. Normal subsequent indexing removes prior source entries through the existing deleted-file path; registration rejects a preexisting indexed candidate instead of claiming immediate remediation.

Source reads preserve root-containment and their existing not-found result for a protected file. Static reads also deny recognized private material inside the web root. Missing referenced files remain protected by stored canonical path and do not make local-only indexing require an available key.

**Alternative rejected:** Storing no key bytes in the new table does not prevent a symlink, source read, or static traversal from returning the original file.

### D7: Thin bounded endpoints and explicit UI state

Implement only the key CRUD shapes defined in the spec, with strict UTF-8/JSON/duplicate-key/type parsing, 8 KiB write-body limit before reading, and no accepted upload/passphrase fields. Reuse `_unique_json_object` and `_reject_json_constant`, already used by transfer and sync. Repository Add/Edit still have permissive JSON parsing; assignment input validation must preserve omission/null/string distinctions and reject invalid selections before mutation rather than inherit a malformed-input-to-empty-object fallback.

Extend `_OPERATION_ERRORS` and `_OPERATION_PHASES` with explicitly supported SSH categories and fixed phases before constructing new domain errors: `RepositoryOperationError` currently normalizes unknown codes to `repository_operation_failed` and unknown phases to absent. Use its bounded messages/status mapping, `safe_operation_error`, REST `_send_repository_error`, CLI `print_repository_failure`, and MCP safe JSON text-content projection rather than a parallel error contract. Add `ssh_key_*`, `ssh_override_conflict`, `ssh_config_unsupported`, and `ssh_http_forbidden` categories as needed; never expose keygen/SSH diagnostics or config contents. Preserve actual add/edit-only `config_saved` context and standalone sync omission. Ordinary repository responses add only key IDs.

The new tab displays names, server paths, fingerprints/algorithms, and associated repository links. Add/Edit selectors use System SSH or stable key IDs and continue through `saveRepositoryOperation`; preserve the saved-add PUT retry path and carry an explicit assignment into that retry. Disable assignment for non-SSH remotes, require explicit clearing on a bound protocol change, and refuse to submit an accidental null when profiles fail to load. Rename/remove refresh all selectors/cards; in-use conflicts identify blocking repos and preserve state. Key path changes are not in-place edits.

Use text nodes/attribute-safe existing helpers, generation guards for late loads, and existing operation locks/strict refresh extended to the new controls. Keep key CRUD response validation separate from `repositoryRequest`'s repository/sync success validators, while reusing safe failure presentation and confirmed-versus-unconfirmed outcomes. Preserve theme/search scope and report successful persistence separately from refresh failure. Extend `tests/test_web_repo_sync.mjs` for selector/retry/refresh integration instead of introducing another browser framework. On non-loopback servers show a clear unavailable state and do not present denied key reads as an empty successful catalog.

**Alternative rejected:** Browser-file inputs suggest copying a key from the browser device; the feature references the server filesystem. A failed list request mapped to an empty selector can silently clear a binding.

## Risks / Trade-offs

- **OpenSSH configuration is flexible and identities accumulate.** -> Use target-scoped effective-config materialization, preserve hop configuration separately, verify effective invariants, and fail explicitly on unsupported/custom launchers.
- **Agent inheritance depends on how the server starts.** -> Explain server-side sockets, external `ssh-add`, and service/container limitations; never claim the browser's terminal session is available.
- **Path-based keys can change between inspection and SSH use.** -> Revalidate before each remote use and require a trusted account/filesystem; this is not a hostile same-account process isolation mechanism.
- **POSIX ownership/modes and OpenSSH format limit portability.** -> State first-version support plainly; unsupported management fails explicitly while System SSH behavior remains available for trusted direct operations.
- **Loopback policy changes existing cross-origin/remote SSH integrations.** -> Mark the compatibility break, preserve non-SSH remote behavior, and document local deployment/tunnel options instead of exposing unauthenticated keys.
- **Catalog replacement can erase bindings.** -> Use UPSERT and a separately managed association table, with reopen/status-save/deletion/transfer regression tests.
- **Already indexed secrets predate this feature.** -> Reject registration of an indexed candidate and require source/index removal; do not market metadata registration as secret remediation.
- **Parent timeout alone can leave SSH children alive.** -> Own/terminate the operation process group and clean ephemeral configuration before reporting completion.
- **Both changes modify the same transport requirement.** -> Carry the prerequisite's full compatibility amendment forward in this delta and sync/archive the prerequisite first.

## Migration Plan

`fix-repository-sync-failures` is already applied, with its implementation still in the current worktree. This change's first task rechecks its completed tasks and targeted behavioral coverage as a regression checkpoint, not a second implementation phase; artifact-existence status alone is not implementation completion. Its MCP transport delta has not yet reached the durable spec, so preserve that full amendment here and synchronize/archive the prerequisite before this change's transport delta. Do not introduce a custom OpenSpec metadata dependency key; record the baseline here, in the proposal, and in tasks.

Create the two catalog tables idempotently, without changing existing repository/transfer columns. Existing repositories start unassigned. Deploy listener/request guards, protected-file checks, backend identity selection, CLI/MCP schema changes, and UI together before exposing key provisioning.

Before enabling non-loopback deployment after upgrade, users must either keep a catalog with no profiles and no HTTP SSH network use, or serve the credential-capable dashboard on loopback. Explain stronger local Host/Origin/CORS behavior and that managed identities reject conflicting custom launchers.

Rollback must not silently make an assigned repository use System SSH. Before downgrading to a version unaware of assignments, explicitly clear bindings and remove profile metadata through the current UI while preserving user files, or refuse the downgrade. Additive unused tables can remain; no private files are deleted. Normal artifact sync/archive occurs only after implementation completion, with the prerequisite's transport delta applied first.
