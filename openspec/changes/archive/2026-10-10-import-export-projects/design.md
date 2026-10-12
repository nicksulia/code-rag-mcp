# Design

## Context

See `proposal.md` for motivation and `specs/project-transfer/spec.md` for the behavior contract.

- The vanilla JavaScript UI manages repositories, groups, and dependencies through the standard-library HTTP handler in `src/server/api.py`; there is no separate project model.
- `RepoManager` owns `repositories`, `repo_groups`, `repo_group_members`, and `repo_dependencies` in `catalog.db`. Its public mutation methods open and commit separate SQLite connections.
- `register_repo` normalizes local paths against the host and calls `save_repo`, which uses `INSERT OR REPLACE`. Calling it for an import could replace an indexed repository's metadata and prevent whole-document rollback.
- Existing relation integrity checks reject unknown repositories, duplicate group creation, self-dependencies, and cycles. Imports must preserve these constraints while merging existing group memberships.
- `MultiRepoRAGService.add_repository` synchronizes by default. Configuration transfer must bypass this behavior.
- The UI has reusable modal and loading patterns, but repository cards, group filters, group management, and dependency controls interpolate metadata into HTML and inline handlers. `escapeHtml` currently does not escape quotes, and existing load helpers can log errors without propagating them.
- Existing tests use `unittest`, temporary catalogs, and request handlers backed by `BytesIO`. There is no checked-in frontend package manifest or browser test framework.

## Goals / Non-Goals

**Goals:**

- One canonical configuration projection and one import planner shared by preview and commit.
- A consistent export snapshot and atomic import despite the threaded HTTP server.
- Preserve existing indexed repositories by never updating existing repository rows during import.
- Keep metadata-only transfer independent of source availability and embedding configuration.
- Treat imported metadata as untrusted in every UI view it reaches.

**Non-Goals:**

- Full database/index backup, automatic synchronization, repository selection for export, workspace switching, or browser-preference transfer.
- Rename/overwrite conflict policies, destructive catalog replacement, or automatic local-path mapping.
- Git authentication provisioning, new CLI/MCP surfaces, or a general REST authentication redesign.
- Introducing a frontend framework, generic job system, or separate transfer database.
- Feature documentation in `README.md`; necessary user guidance stays in the transfer UI, and the format/API contract stays in the OpenSpec artifacts.

## Decisions

### D1: A single versioned configuration projection

Use the exact version 1 fields in the spec, not `Repository.to_dict()`, which includes runtime metadata. A representative file is:

```json
{
  "version": 1,
  "repositories": [
    {
      "repo_id": "api",
      "name": "Backend API",
      "source_type": "git",
      "url_or_path": "git@example.com:team/api.git",
      "branch": "main"
    },
    {
      "repo_id": "web",
      "name": "Frontend",
      "source_type": "local",
      "url_or_path": "/work/team/web",
      "branch": null
    }
  ],
  "groups": [
    {"name": "platform", "members": ["api", "web"]}
  ],
  "dependencies": [
    {"repo_id": "web", "depends_on_repo_id": "api"}
  ]
}
```

IDs and group names are case-sensitive. Trim textual configuration except local paths; retain local-path spelling without `abspath`, `expanduser`, or filesystem access. Do not substitute missing branches or display names with defaults: required fields make equality explicit, while `null` supports catalog entries without a branch. Canonicalize both incoming and existing configuration before comparison.

For export, read all four catalog tables on one SQLite connection inside an explicit read transaction. Project and sort the rows, validate the resulting document, and serialize centrally as UTF-8, two-space-indented JSON with a trailing newline and stable object-field order. Validate catalog integrity too; unsupported legacy entries, dangling relations, or cycles must be explained, not dropped.

**Alternative rejected:** Exporting the existing REST list verbatim mixes configuration with derived state and makes byte-stable export impossible.

### D2: Small, strict validation using existing standard-library facilities

Keep transfer normalization and validation close to the catalog in `repo_manager.py`; use typed result structures in `src/models/schema.py` only where shared results need them. Do not introduce a generic serialization framework or new dependency.

Validate exact keys, types, version, safe ID format, uniqueness, references, and credential-free Git address forms. Reuse the existing relation error vocabulary and extract small shared cycle/integrity helpers when needed rather than maintaining incompatible rules for ordinary writes and imports.

Use strict JSON decoding with duplicate-object-key detection at the HTTP boundary. Handle parsing errors, UTF-8 decoding errors, and invalid shapes explicitly; the new import route must dispatch before the current POST parser that silently converts malformed JSON to `{}`. Reject unsupported Git address forms, including remote-helper syntax, rather than accepting arbitrary values that Git might interpret later. Supported forms are HTTP(S), SSH, `git://`, and ordinary scp-style Git addresses; HTTP(S) user information/query/fragment and SSH passwords are forbidden. Never echo raw rejected addresses.

The browser keeps the original validated-by-server file text. When wrapping it for preview or confirmation, it embeds the original JSON text under `document`, instead of parsing and reserializing it first; otherwise duplicate JSON object keys would be lost before the server could reject them. The server remains authoritative for content validation.

Chosen limits are 1 MiB for selected/exported JSON files and 2 MiB for raw import HTTP bodies, allowing envelope overhead while bounding direct requests. Check file size before `File.text()` and request size before `rfile.read()`. These are product defaults for this change, not limits inferred from existing code.

**Alternative rejected:** Permissive coercion, ignored unknown fields, or credential redaction could silently change an imported definition or produce an export that does not recreate the original configuration.

### D3: Additive merge with explicit conflict handling

Build the import plan from a normalized document plus a catalog snapshot:

- An absent repository ID becomes an insertion.
- Matching configuration becomes an unchanged repository, regardless of runtime fields.
- A differing configuration for an existing ID becomes a conflict listing the ID and differing field names, not a proposed overwrite.
- Group names identify reusable groups. Plan only missing groups and memberships; never remove destination members.
- Plan only missing dependency edges.
- Validate references against the union of document and destination repositories and cycles against the union of existing and incoming edges.

The planner returns a stable summary:

```json
{
  "can_import": true,
  "additions": {
    "repositories": {"count": 1, "ids": ["web"]},
    "groups": {"count": 0, "names": []},
    "memberships": {
      "count": 1,
      "entries": [{"group": "platform", "repo_id": "web"}]
    },
    "dependencies": {
      "count": 1,
      "entries": [{"repo_id": "web", "depends_on_repo_id": "api"}]
    }
  },
  "unchanged_repositories": ["api"],
  "conflicts": [],
  "warnings": [{"repo_id": "web", "code": "local_path_machine_specific"}]
}
```

Conflicts use `{"repo_id": "...", "fields": ["branch"]}`. Warnings identify repositories without echoing source values. Counts reflect operations, not the number of entries present in the file.

**Alternative rejected:** "Skip conflicting rows and import the rest" produces partial restores and makes dependency/group semantics harder to reason about. Replace-all behavior is destructive and outside scope.

### D4: Transaction-scoped confirmation, not a sequence of public mutations

Preview uses an explicit read transaction and performs no writes. Confirmation opens one connection, starts `BEGIN IMMEDIATE`, reloads the current catalog, reruns normalization/planning and integrity validation, then inserts all additions and commits once. Conflicts or any database error roll back the whole transaction.

Insert new repository rows explicitly with the existing READY registration status, zero counts, and null commit/sync/error fields. Do not call `save_repo`, `register_repo`, `create_group`, or `add_dependency` as a sequence of separately committed operations. Refactor only the small connection-aware helpers required to share existing integrity rules.

Because confirmation never modifies existing repository rows, it cannot reset their runtime metadata or derived indexes. SQLite serializes writes during confirmation; errors such as a locked database are surfaced rather than silently retried as success.

Preview is advisory and stateless: no preview token, stored server session, or reservation. Confirmation always validates again. If intervening changes are still compatible, report the actual additions (which can be fewer than previewed); if they introduce conflicts or an invalid graph, reject without mutation.

**Alternative rejected:** Client-side replay of existing registration/group/dependency requests cannot roll back and uses unsafe replacement semantics. A process-local lock alone would not protect other SQLite connections.

### D5: Thin service and REST surfaces

Add metadata-only service delegation methods and these endpoints:

| Endpoint | Request | Success |
| --- | --- | --- |
| `GET /api/v1/projects/export` | None | Canonical document bytes; `200`, JSON content type, `Content-Disposition: attachment; filename="projects.json"` |
| `POST /api/v1/projects/import` | `document` and optional boolean `dry_run` (default `true`) | Preview: `200`, `dry_run: true` plus plan; commit: `200`, `dry_run: false`, `success: true` plus actual plan |

The envelope allows no other keys. Map parse/schema/integrity/export validation failures to `400`, confirmation conflicts to `409`, request size violations to `413`, and unexpected catalog/persistence failures to `500`. Retain the repository-standard `{"error": "...", "status": ...}` error shape and log unexpected failures server-side without source/credential values. Do not use broad success-shaped defaults.

Serialize export centrally and send those exact bytes from the HTTP handler so size validation, deterministic exports, and the downloaded file match. Add attachment support only where required; do not change the serialization of unrelated endpoints.

These routes belong to the existing REST/UI deployment trust model. MCP token authentication currently covers `/mcp`, not REST. This change neither extends that token's meaning nor claims to secure a publicly exposed REST server.

**Alternative rejected:** Browser-generated export from several list requests could combine inconsistent snapshots and accidentally rely on swallowed request errors.

### D6: A modal transfer flow with explicit state and errors

Place both controls in the Repositories hub's header. Export checks HTTP success, creates a Blob download, uses the constant filename, and revokes the temporary object URL after use.

The import modal uses a JSON file input, server preview, local-path warning, confirm button, cancel button, and visible status/error area:

```text
idle --> previewing --> preview-ready --> importing --> success
             |               |               |
             v               v               v
           error          canceled          error
```

Track the selected raw file text and a monotonically increasing request generation. Replacing/canceling a selection invalidates outstanding previews, so late responses cannot enable confirmation for a different file. Do not let modal closure or duplicate clicks launch a second commit while one is in flight.

After commit, display the actual summary and explain that configuration is not indexed. Refresh groups, repositories/relations, statistics, and the scope summary; preserve theme and valid scope selections. Existing transfer refresh calls must observe non-OK responses and load failures even though current loaders can swallow them. Distinguish "import persisted, refresh failed" from "import failed" so the user is not encouraged to repeat a successful operation blindly.

Use text nodes/`textContent` for untrusted fields. For existing repository/group templates, apply context-appropriate attribute escaping and replace dynamic inline `onclick`/`onchange` handlers with bound listeners or delegation. Audit every affected card/filter/group/dependency view, not just the preview. In-memory repository-relation maps must also handle arbitrary valid IDs such as `constructor` and `toString` as ordinary keys, using `Map` or a null-prototype dictionary where appropriate.

**Alternative rejected:** Escaping HTML text alone does not make quoted attributes or interpolated JavaScript handlers safe.

### D7: Focused, behavior-level verification

Add catalog tests in `tests/test_project_transfer.py` and HTTP tests in `tests/test_api_project_transfer.py`, following the existing temporary-directory and `BytesIO` handler patterns. Use mock source/sync/embedding operations to prove import never invokes them. Avoid instantiating or reaching external services for transfer tests.

Cover round trips into a fresh catalog and into an indexed destination, deterministic export, empty data, null branches, Unicode display names, strict duplicates/types/limits, URL credential rejection without leaks, group unions, references to destination-only repositories, combined-graph cycles, idempotency, conflicts, revalidation after preview, and forced mid-transaction rollback. Reopen the catalog after success and after rollback to verify persistence.

Use the existing relation, API, update, scope, and MCP tests as targeted compatibility coverage. Check JavaScript syntax and exercise the browser workflow, including download/reimport, cancellation, file replacement during preview, failure recovery, local-path warnings, preserved selections, and literal rendering of hostile-looking metadata. If browser automation is available, use it without adding a frontend dependency stack; otherwise perform and record these checks as manual UI verification.

## Risks / Trade-offs

- **Local paths are not portable.** -> Preserve them without filesystem access, warn before confirmation, and use the existing edit UI before syncing.
- **Legacy catalogs may contain unsafe IDs or credential-bearing Git URLs.** -> Block export with field-level guidance; never silently drop a repository or strip credentials into a misleading round trip.
- **Strict format and size limits exclude arbitrary legacy JSON/list dumps.** -> Keep the exact version 1 format and limits in the OpenSpec contract; unsupported files fail with explicit UI errors.
- **Preview can become stale.** -> Replan inside the commit transaction and report actual additions; never trust prior client validation.
- **Imported metadata reaches existing unsafe templates.** -> Include tightly coupled rendering changes and check all affected metadata surfaces, including prototype-like IDs.
- **Configuration reveals private repo names, hostnames, and local paths.** -> Treat exported configuration as private even when credential-free; sharing remains an explicit user action.
- **REST remains governed by its existing deployment model.** -> Preserve trusted/local deployment assumptions without presenting MCP-token authentication as protection for transfer routes.
- **No checked-in browser test harness exists.** -> Keep backend regression coverage automated and require explicit browser verification rather than adding broad tooling.

## Migration Plan

No schema migration: the existing catalog tables already represent all transferred data. Deploy the backend and static assets together; old installations gain transfer controls without changing stored entries.

Rollback removes the new routes and UI controls. Successfully imported entries remain ordinary catalog data usable by the prior version; rollback does not delete them or their subsequently created indexes. Transfer format version 1 remains the contract for saved files. `README.md` remains unchanged.
