# Design

## Context

See `proposal.md` for motivation and the delta specs for the observable contract.

- `RepoManager.clone_or_pull_git_repo` checks subprocess return codes but swallows all failures for an existing checkout. Its normal clone/fetch/checkout/pull calls have no timeout.
- `switch_git_branch_or_url` has timeouts, but its fetch and pull calls do not check return codes. It returns raw stderr and exception text on other failures.
- `update_repo` records requested configuration and error status when a Git update fails. It can perform remote/branch operations even when the service is called with `auto_sync=False`; that flag currently skips indexing, not all Git operations.
- `MultiRepoRAGService` registers or edits configuration before automatic sync. `_sync_repository_impl` prepares Git before scanning or changing indexes, then persists a ready status and `last_synced_at` on successful indexing. Its exception path already persists an error status. Provenance is written only after successful sync.
- REST sync currently ignores request options, returns raw exception text on failure, and uses the ordinary POST parser that converts malformed JSON to an empty object. Add/edit responses do not explain persistence after failed automatic sync.
- Browser Sync and Sync All ignore HTTP errors. Sync All only logs network failures and lacks outer `finally` protection. Repository cards display source type but not persisted status/error. Existing repository/graph loaders have a strict failure-propagation mode introduced by project transfer; reuse that mode.
- CLI sync continues after exceptions but exits successfully. Add prints the supplied source address; sync prints stored source addresses; update prints raw source metadata and catches exceptions without a failed exit.
- MCP tool errors already support `isError: true` and text content but stringify arbitrary exceptions. Both HTTP and stdio invoke the same MCP implementation.
- Durable specs have no repository-sync capability. The transport spec's historical absolute "stdio unchanged" clause must be narrowed explicitly rather than silently contradicted.
- Tests use `unittest`, temporary catalogs, mocked subprocesses, a subword embedding provider, and in-memory HTTP handlers. The frontend is vanilla JavaScript without a checked-in browser test framework. The worktree contains a separate import/export implementation; integrate with its helpers without reverting it.

## Goals / Non-Goals

**Goals:**

- One checked Git execution/error boundary used by both sync and repository-update paths, with no per-endpoint authentication logic.
- Stop failures before the existing destructive indexing steps and preserve state at that boundary.
- Represent remote sync, explicit checkout indexing, and saved-configuration failures distinctly without adding catalog columns.
- Keep successful default service/REST/MCP results compatible and keep execution settings scoped to subprocesses.

**Non-Goals:**

- Atomic rollback across vector, lexical, graph, and catalog stores after indexing begins, or undoing completed Git working-tree operations.
- Changing branch selection, merge strategy, repository identity resolution, index filtering, or `auto_sync=False` Git-update semantics.
- A generic process framework, job queue, new frontend dependency stack, configurable timeout system, or automatic retry policy.
- SSH-key custody, key selection, REST security redesign, or changing the existing embedding-model offline fallback.

## Decisions

### D1: A small checked Git runner with safe domain errors

Add a focused helper beside the current Git operations in `repo_manager.py`. It executes argument lists without a shell, with checked exit codes, captured output, disabled stdin, a per-command timeout, and a fresh copy of the inherited environment. Clone/fetch/pull use 120 seconds; remote set-url, checkout, and local Git inspection use 15 seconds.

Disable Git terminal prompts and askpass interaction for these subprocesses. Use noninteractive OpenSSH settings for the ordinary SSH launcher while retaining agent access, host aliases, identity files, proxy settings, and known-hosts configuration. Do not silently replace a user-provided `GIT_SSH_COMMAND`/`GIT_SSH` launcher: preserve its intended invocation, disable interactive input where supported, and bound it by the same deadline. Never rewrite user configuration, mutate `os.environ`, autoaccept host keys, or disable verification. Custom credential/SSH launchers must be preconfigured for unattended use; an interactive launcher is not made usable by a browser sync.

Catch expected process failures (`CalledProcessError`, `TimeoutExpired`, and relevant `OSError` cases) at this boundary. Raise a Git-operation domain error containing only safe code, phase, public message, and exit outcome. Classify a bounded portion of diagnostics against a small whitelist of recognizable signatures; never include the diagnostic text in the error or logs. Unknown diagnostics produce a generic error rather than a guessed authentication/network cause.

| Code | Evidence / meaning | HTTP |
| --- | --- | --- |
| `git_authentication_failed` | Recognized credential/access-denied diagnostic | 500 |
| `git_host_key_failed` | Recognized host verification/unknown-host diagnostic | 500 |
| `git_timeout` | The subprocess deadline expires | 504 |
| `git_connection_failed` | Recognized DNS/connection diagnostic | 500 |
| `git_unavailable` | The Git executable cannot be started | 503 |
| `git_command_failed` | Other checked Git failure | 500 |
| `checkout_unavailable` | Missing/unusable cached checkout requested explicitly | 400 |
| `invalid_sync_options` | Invalid request or option type | 400 |
| `repository_not_found` | Requested repository cannot be resolved | 404 |
| `repository_operation_failed` | Unclassified indexing/persistence failure | 500 |

Phases use fixed names such as `clone`, `fetch`, `checkout`, `pull`, `set_remote`, `inspect_checkout`, `index`, and `save_configuration`; do not embed command arguments. Public messages are fixed templates limited to 512 characters. Log safe repository/phase/code/exit fields, not command arrays, raw output, tracebacks containing Git exception text, or source addresses.

Replace the swallowed fallback and unchecked calls with this runner. Preserve existing successful manager return values where useful to existing callers/tests; propagate typed exceptions on failure instead of false/raw-string results. A failed branch fetch cannot proceed to checkout or pull. Reuse the existing tracking-branch fallback only after a successful fetch and a checked checkout failure that warrants that fallback, not after an authentication/network failure.

**Alternative rejected:** Fixing only `check=True` or removing one `pass` leaves timeouts, unsafe diagnostics, and the parallel branch-update path inconsistent. Global environment changes are unsafe on the threaded server.

### D2: Keep the existing pre-index boundary and add explicit local-only mode

Add `local_only: bool = False` to the service sync entry points and validate both it and `force` before starting repository work. Default Git sync still performs the current required remote preparation, and only then enters scanning and index changes. On a pre-index Git error, save only error status/message on the current repository record; do not change its stored indexed commit, counts, successful-sync timestamp, hashes, indexes, or provenance.

For `local_only=True` on a Git repository, verify the clone root and inspect its actual HEAD using bounded local Git commands. Do not clone, fetch, pull, switch to the configured branch, or change the remote. Missing/unusable HEAD produces `checkout_unavailable` before index mutation. Feed that checkout through the same index pipeline so filtering, incremental hashes, embeddings, counts, and provenance behave identically.

Successful local-only Git indexing returns the current metrics with `status: "indexed_local"` and preserves `last_synced_at`. Ordinary successful sync retains `status: "synced"` and its existing fields. Local-directory repositories retain their current successful semantics. `force` affects scanning only and never skips remote preparation.

The ready status continues to mean the index is usable, not proof that its remote was just contacted. Display the distinct operation result and label any Git successful-sync timestamp as **Last remote sync**; no new status enum or timestamp column is required. A successful local-only operation can clear the operation error while leaving the remote-sync timestamp unchanged.

**Alternative rejected:** Automatic fallback cannot distinguish fresh remote content from cached content. A new cached-index endpoint/tool duplicates the existing indexing orchestration; one explicit option preserves it.

### D3: Attach persistence context without pretending Git updates are transactional

Use a repository-operation error wrapper with safe serializable fields and optional `config_saved`. Standalone sync does not set this field.

After registration or update actually commits, attach `config_saved=True` to a subsequent Git/indexing error, including the existing failure-save path inside `update_repo`. If a configuration save fails, report `config_saved=False` only when that outcome is known. Do not claim persistence certainty from a lost network response.

Retain currently intended persistence of requested configuration on Git-update failure, along with the error status; this is desired configuration for retry, not a claim that checkout or remote application completed. Preserve `auto_sync=False` behavior: it suppresses indexing but does not newly suppress Git branch/remote operations already performed by updates. Successful add/edit shapes remain unchanged.

Sanitize unexpected failures within these repository-operation boundaries too. Log their type and safe context without raw exception text, and expose `repository_operation_failed`. Catching and rethrowing a visible safe failure is not a success fallback.

**Alternative rejected:** Rolling configuration back after arbitrary Git side effects creates another misleading state and expands this work into distributed rollback. Returning HTTP success merely because configuration saved hides the requested operation failure.

### D4: Thin REST and MCP adapters, deliberate CLI failure exits

Dispatch the sync route before the permissive ordinary POST parser. Use the existing strict JSON helpers from project transfer for duplicate-key and invalid-constant rejection. Accept an empty body or exactly the optional boolean fields `force` and `local_only`; require an object otherwise. Apply a small 4 KiB sync-body limit before reading, returning `413` for excess input. Do not refactor unrelated POST parsing.

Map safe domain errors using D1. Retain `{"error": "...", "status": ...}` and add the applicable `code`, `phase`, `repo_id`, and known add/edit `config_saved` fields. Use the same projection in add/edit exception handling; do not catch a typed error and replace it with `str()` of its cause. Existing unrelated REST errors, CORS, MCP authentication, and transfer routes are unchanged.

Extend the MCP sync schema and execution path with `local_only`. Validate booleans explicitly because MCP does not automatically enforce its advertised schema. For repository-operation errors, keep existing `result.isError: true` and text-content framing, with the text containing the JSON serialization of the same safe error fields (without REST HTTP status). Other tool errors remain unchanged. Both transports therefore share outcomes without transport-specific Git logic.

CLI sync/add/update return meaningful exit outcomes that `main` applies after its existing `service.shutdown()` cleanup. Sync continues through its selected list and prints aggregate success/failure counts; unknown requested repositories exit nonzero. Add/update catch safe operation errors and show persistence context. Remove raw Git source-address printing from these affected paths; repository ID/name and safe operation context are enough. Do not add success logs or raw exception tracebacks for failure.

**Alternative rejected:** A browser-only fix leaves scripts and MCP tools claiming success; a new generic API/error framework changes unrelated surfaces unnecessarily.

### D5: Explicit UI outcomes and reuse strict refresh helpers

Add a compact live status/results area to the Repositories hub and a text-safe persisted status/error area on repository cards. Add a distinct **Index existing checkout** action only for Git entries with clear no-remote wording; do not turn Sync or Sync All into automatic cached indexing.

Extract one narrowly scoped repository-operation request helper that checks HTTP status, parses the expected payload/error, and carries known server errors separately from unconfirmed network/invalid-response outcomes. Use it for single sync, cached indexing, and Sync All, and reuse its error projection in existing Add/Edit handlers.

Sync All captures the current list once, processes it sequentially as it does today, records each outcome, and continues after errors. Display counts for succeeded, failed, and unconfirmed requests plus repository-level results. Keep results independent of refreshing the hub. Prevent duplicate starts and conflicting clicks for the affected browser controls while a run is in flight; restore them in an outer `finally`, including when strict refresh fails.

Refresh repository status after known operation failures as well as success using `loadRepositories({strict: true})` and the existing strict graph-refresh support. Display refresh failures separately, never replace already known outcomes. Preserve scope/theme and do not let a rerender discard the run summary.

Add/Edit modals show **Configuration saved; sync/update failed** for `config_saved=True`, refresh the registered entry, and retain fields for correction. Where persistence is unconfirmed, instruct the user to refresh/check the catalog before repeating registration. Do not close a modal with a failure as though the full operation succeeded.

**Alternative rejected:** Alerts alone cannot show bulk outcomes or saved-configuration context. Reusing current non-strict loaders would still silently hide post-operation refresh failures.

### D6: Verify failures and compatibility without real external services

Add focused `unittest` coverage in `tests/test_repo_sync.py`, `tests/test_api_repo_sync.py`, and `tests/test_cli_sync.py`; extend existing update/MCP tests where those behaviors are already covered. Follow temporary-directory, mocked subprocess, subword service, and in-memory HTTP-handler patterns. No test requires real SSH credentials, a remote Git provider, or an embedding service.

Use mocked command sequences to prove that failing fetch/checkout/pull stops the exact later commands and prevents scanning/index mutations. Reopen catalogs and compare old hashes/counts/commit/timestamp/provenance and index content on failure. Inject secret-bearing arguments, stderr, timeout output, and unexpected exception messages; assert absence from every affected error and captured application log/CLI output.

Assert the exact 120/15-second deadline values and environment isolation, strict request types and duplicate keys, the 512-character message bound, and the 4 KiB HTTP boundary. Mock timeouts instead of waiting for them.

Exercise local-only indexing and missing checkouts, successful default result compatibility, force semantics, branch/remote update failures, add/edit persistence context, CLI partial-failure exit codes, and equivalent stdio/HTTP MCP tool errors. Check JavaScript syntax and exercise browser single/bulk/cached/save flows, refresh errors, duplicate clicks, theme/scope retention, and text-safe error rendering without introducing a frontend framework.

**Alternative rejected:** Testing only that a method raised misses stale-index mutation, swallowed HTTP errors, successful CLI exits, or secret-bearing diagnostics at other surfaces.

## Risks / Trade-offs

- **Existing offline workflows change deliberately.** -> Document and surface `--local-only` / Index existing checkout before removing implicit fallback; retain default successful response shapes.
- **Finite Git deadlines can reject legitimately slow operations.** -> Use a distinct timeout code and explicit retry guidance; fixed 120/15-second defaults keep the change bounded rather than add configuration machinery.
- **Git may change the working tree before a later failure.** -> Preserve prior indexes, report the failed phase, and do not promise Git rollback or remote freshness from cached indexing.
- **Different Git/SSH versions produce different diagnostics.** -> Classify only supported patterns; generic safe failure remains correct when classification is uncertain.
- **Custom credential/SSH launchers can require interaction.** -> Require unattended configuration, disable prompts/input where supported, and enforce finite deadlines without replacing the user's launcher silently.
- **Exceptions can leak through wrapping, debug logging, or CLI banners.** -> Centralize safe projections, avoid raw traceback/diagnostic logging for repository operations, and use sentinel-secret assertions across all error surfaces.
- **Requests or refreshes can leave ambiguous client state.** -> Separate server-confirmed outcomes from unconfirmed requests and refresh failures; do not encourage duplicate registration.
- **Indexing after remote preparation is still not atomic.** -> Restrict the preservation guarantee to failures before index mutation; transactional index recovery is separate work.
- **Historical MCP compatibility wording is absolute.** -> Include the explicit narrow transport delta and keep unrelated protocol/security behavior unchanged.

## Migration Plan

No catalog migration or new dependencies are required. Ship backend, CLI/MCP schemas, static assets, and README guidance together. Existing entries retain their configuration and indexed content; older callers posting no sync body continue to use default remote mode.

Record the breaking behavior corrections: failed remote preparation no longer succeeds from cache, sync bodies are validated, and CLI failures now exit nonzero. Offline users opt into local-only indexing. Main durable specs are not edited during proposal creation; these delta specs are synchronized through the normal completed-change workflow.

Rollback removes the new local-only option and UI action and restores the prior implementation without deleting repositories/indexes. Existing error statuses remain readable by the prior version; rollback reintroduces the original reliability bugs and is not an automatic recovery policy.

The future `add-repository-ssh-keys` change consumes this checked-operation/error contract and separately establishes key-management/key-use security. It is neither scaffolded nor implemented by this change.
