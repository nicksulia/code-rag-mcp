# Spec Delta

## Purpose

Let users register existing server-side SSH key references and manually select an identity for each SSH Git repository, while retaining inherited system authentication when unassigned and keeping private-key material out of the application's stored and returned data.

## ADDED Requirements

### Requirement: Named server-side key references

On supported POSIX/OpenSSH installations, the system SHALL register a profile containing a stable `key_id`, trimmed nonempty `name` of at most 80 characters, canonical `private_key_path`, SHA256 public `fingerprint`, and `algorithm`. Paths SHALL be absolute or start with `~/`, expanded using the server account, and contain at most 4,096 UTF-8 bytes with no control characters. Names SHALL reject control characters.

The referenced file SHALL exist, be a readable regular OpenSSH private-key file owned by the server account, and grant no group/other permissions. Public-key-only files and unsupported private-key formats SHALL be rejected explicitly. Duplicate canonical paths or public fingerprints SHALL be rejected as conflicts. Registration SHALL derive public metadata locally with a 15-second deadline without contacting a Git server, prompting for passphrases, copying private-key material, or changing the file/agent/configuration.

#### Scenario: Register an existing key
- **WHEN** a user supplies a valid name and supported private-key path
- **THEN** the system saves a profile with canonical path and public fingerprint
- **AND** no private-key contents are stored or uploaded and no remote authentication occurs

#### Scenario: Unsafe or unsupported file
- **WHEN** the path identifies a public key, unsupported format, unreadable file, another user's file, or file with group/other permissions
- **THEN** registration fails with a safe actionable error and saves no profile
- **AND** the system does not silently change permissions or echo file contents

#### Scenario: Duplicate identity
- **WHEN** a profile already references the same canonical path or public fingerprint
- **THEN** registration returns a conflict rather than another profile
- **AND** the existing profile and repository associations remain unchanged

### Requirement: Profile lifecycle preserves user files

The system SHALL list profile metadata with associated repository IDs, permit renaming a profile, and permit deletion only when it has no associated repositories. Profiles SHALL be ordered by name and key ID for stable display. Deleting a profile SHALL remove application metadata only, never the user's private/public key files, agent identities, or SSH configuration.

Private-key path or fingerprint replacement SHALL require registration of a new profile and explicit reassignment; renaming SHALL NOT replace the underlying identity. Deletion of an associated profile SHALL fail with a conflict identifying the blocking repository IDs.

#### Scenario: Remove an unused reference
- **WHEN** an unassociated profile is deleted
- **THEN** its catalog metadata disappears
- **AND** the key files and SSH agent remain untouched

#### Scenario: Remove an associated reference
- **WHEN** a user attempts to delete a profile assigned to repositories
- **THEN** deletion fails with a conflict identifying those repositories
- **AND** no association or user file is removed

### Requirement: Manual optional repository assignment

Repository configuration SHALL expose nullable `ssh_key_id`, defaulting to null. A profile SHALL be assignable to multiple repositories, but each repository SHALL have at most one assigned profile. Assignment SHALL be permitted only for registered Git repositories with supported SSH or scp-style remotes.

On add, omitted or null `ssh_key_id` SHALL mean System SSH. On update, omission SHALL preserve the current assignment, null SHALL explicitly clear it, and a string SHALL assign that existing profile. Invalid IDs, wrong types, and assignments to HTTPS, git-protocol, or local sources SHALL be rejected before configuration mutation or Git execution. An update from SSH to an ineligible remote SHALL require explicit clearing of an existing assignment in the same request.

Repository metadata and an explicitly changed assignment SHALL be saved atomically before any initial clone or branch/remote operation. Ordinary indexing/status saves SHALL NOT change assignments. Deleting a repository SHALL remove its association but retain the profile and user files.

#### Scenario: First clone uses the selected key
- **WHEN** a user registers an SSH Git repository with a valid profile and automatic sync
- **THEN** its profile association is saved before the first clone
- **AND** the clone uses that profile, not a temporary default identity

#### Scenario: Omitted versus cleared assignment
- **WHEN** an edit omits `ssh_key_id`
- **THEN** its existing assignment remains unchanged
- **AND** a subsequent edit explicitly supplying null switches it to System SSH

#### Scenario: HTTPS transition requires clearing
- **WHEN** an assigned SSH repository is edited to use an HTTPS remote without clearing its key assignment
- **THEN** the edit is rejected before mutation or network access
- **AND** including `ssh_key_id: null` permits the ordinary HTTPS update

### Requirement: Unassigned repositories use inherited system SSH

An unassigned repository SHALL retain the running server or trusted CLI process's inherited Git/SSH configuration and SSH agent connection, subject to the sync-reliability noninteractive/deadline contract and the HTTP credential boundary. The application SHALL NOT discover or automatically associate a key, start a new agent, run `ssh-add`, or mutate user configuration.

Inherited authentication SHALL refer to the server process, not the browser account or an unrelated terminal session. UI guidance SHALL explain that the server must inherit a reachable `SSH_AUTH_SOCK` for keys previously unlocked with `ssh-add`.

#### Scenario: Terminal agent is inherited by the server
- **WHEN** a user loads a key with `ssh-add` and starts the local server from an environment containing that agent's socket
- **THEN** an unassigned repository can authenticate through the inherited agent without registering a profile

#### Scenario: No usable inherited agent
- **WHEN** an unassigned repository cannot authenticate through its actual process environment
- **THEN** sync reports an explicit safe failure
- **AND** the application does not claim access to the browser's agent or silently create one

### Requirement: Assigned identity excludes unrelated identities

All remote Git operations within one repository operation SHALL use a consistent snapshot of its assigned profile, including clone, fetch, pull, and Git preparation during branch/remote updates. Only the selected private key or its matching unlocked agent identity SHALL be offered to the target Git SSH server; unrelated agent identities, configured identity files, certificates for other keys, password authentication, and interactive alternatives SHALL not be used for that target.

Effective host aliases, destination user/port, proxy routing, and known-host verification SHALL remain honored. Managed Git transport SHALL not silently switch to a non-SSH authentication mechanism. Conflicting custom SSH launchers or unsupported configuration SHALL fail explicitly rather than be silently replaced or ignored. Per-operation authentication state SHALL not change the parent environment or affect another repository's operation.

Preconfigured proxy-hop authentication SHALL retain its own user SSH configuration and SHALL not be mistaken for target identity selection or used as a fallback identity at the Git server. Managed target connections SHALL not forward the entire agent to that target.

#### Scenario: Multiple available identities
- **WHEN** the agent and SSH configuration contain keys A and B, but the repository is assigned A
- **THEN** its operation offers only A or the matching agent identity
- **AND** failure of A does not cause an attempt with B, System SSH, or password authentication

#### Scenario: Host alias with selected key
- **WHEN** the remote uses an SSH alias defining a hostname, user, port, and proxy route
- **THEN** those effective destination settings are preserved while authentication is restricted to the selected profile

#### Scenario: Parallel repositories use different profiles
- **WHEN** two repository operations use different assigned profiles concurrently
- **THEN** each retains its own identity settings without global environment changes or mixed-profile operations

### Requirement: Revalidation and encrypted-key failures are explicit

Before each managed remote use, the referenced file SHALL be revalidated for availability, ownership, permissions, supported format, and the registered public fingerprint. A changed fingerprint SHALL fail rather than implicitly rotate the profile. Encrypted keys SHALL be usable only when the matching identity is already unlocked in the inherited agent; the application SHALL not request, store, or retry with a passphrase.

Unavailable, changed, insecure, unsupported, or locked identities SHALL produce safe errors using the prerequisite's repository-operation framing and saved-configuration context. Failed pre-index authentication SHALL preserve prior indexes and successful-sync timestamp. Explicit local-only checkout indexing SHALL not require the assigned profile to be currently usable, because it performs no remote authentication.

#### Scenario: Referenced key changes on disk
- **WHEN** a file's public fingerprint differs from the registered profile before remote use
- **THEN** the operation reports a key-changed error
- **AND** it does not contact the remote with that changed identity or retry with another key

#### Scenario: Encrypted key requires unlocking
- **WHEN** an assigned encrypted key lacks a matching unlocked agent identity
- **THEN** remote synchronization fails noninteractively with safe authentication guidance
- **AND** the previous index remains intact for this pre-index failure

#### Scenario: Cached indexing with a missing key
- **WHEN** a usable existing checkout is indexed explicitly with `local_only: true`, but its assigned key file is missing
- **THEN** local indexing can proceed without reading or attempting to unlock that key
- **AND** the result remains `indexed_local`, not remote-sync success

### Requirement: Private-key material is not application content

The application SHALL not store, return, log, embed, or export private-key material or passphrases. Profile registration SHALL reject a candidate whose private file is already represented in a repository index until that source/index entry is removed. Subsequent source scanning and file serving SHALL protect registered private files, including aliasing through symlinks/hard links, and recognized private-key files regardless of profile membership.

REST/MCP source-file reads of protected key material SHALL use the existing not-found behavior without disclosing contents or external paths. Private paths SHALL appear only in protected local profile-management metadata, not ordinary repository-list metadata, MCP results, or project-transfer files. Public fingerprints and assigned key IDs are permitted metadata.

#### Scenario: Key alias appears inside a source tree
- **WHEN** a source file resolves to or shares the file identity of a registered private key
- **THEN** it is excluded before hashing/chunking/embedding
- **AND** REST and MCP source-file reads do not return its contents

#### Scenario: Candidate key was previously indexed
- **WHEN** registration identifies its private file among existing indexed source entries
- **THEN** registration fails with safe guidance to remove the source and index entry first
- **AND** it does not claim that already indexed private material has been made safe

### Requirement: Project transfer excludes machine-local authentication

Version 1 project documents SHALL keep their existing exact fields and exclude profiles, `ssh_key_id`, private paths, fingerprints, and key material. New imported repositories SHALL use System SSH. An identical existing repository imported again SHALL retain its current association, and transfer comparison SHALL not treat machine-local associations as configuration conflicts. Profile and assignment changes alone SHALL not change exported bytes.

#### Scenario: Configuration moves to another machine
- **WHEN** an assigned repository is exported and imported into a fresh catalog
- **THEN** its repository configuration is preserved without key metadata
- **AND** it is unassigned on the destination until manually configured

#### Scenario: Existing repository is reimported
- **WHEN** an identical repository definition is imported into a catalog where it has an assigned profile
- **THEN** its assignment and runtime/index state remain intact
- **AND** export remains byte-identical when only the assignment changes

### Requirement: Key-management HTTP interface is bounded and metadata-only

The protected local API SHALL expose:

| Endpoint | Request | Success |
| --- | --- | --- |
| `GET /api/v1/ssh-keys` | None | `200`, `{"ssh_keys": [<profile>]}` |
| `POST /api/v1/ssh-keys` | Exactly `name`, `private_key_path` | `201`, `{"ssh_key": <profile>}` |
| `PUT /api/v1/ssh-keys/{key_id}` | Exactly `name` | `200`, `{"ssh_key": <profile>}` |
| `DELETE /api/v1/ssh-keys/{key_id}` | None | `200`, `{"success": true, "deleted_key_id": "..."}` |

Profile responses SHALL contain exactly `key_id`, `name`, `private_key_path`, `fingerprint`, `algorithm`, and sorted `repo_ids`. Write bodies SHALL reject malformed JSON, duplicate keys, wrong shapes/types, unknown fields, and private-key/passphrase content fields. Bodies up to 8,192 bytes SHALL be accepted; larger bodies SHALL return `413` before reading. Invalid inputs SHALL return `400`, unknown profiles `404`, duplicate/in-use conflicts `409`, unavailable SSH tools `503`, validation timeouts `504`, and unexpected failures a safe `500`, retaining the standard `error`/numeric `status` shape plus a supported code.

#### Scenario: Upload-shaped request is rejected
- **WHEN** a request includes `private_key`, `passphrase`, an uploaded file, or another unsupported field
- **THEN** it is rejected without storing key material or partially registering a profile

#### Scenario: Request size boundary
- **WHEN** a key-management body is 8,193 bytes
- **THEN** HTTP `413` rejects it before the body is read
- **AND** an otherwise valid 8,192-byte request remains within the accepted limit

### Requirement: Dedicated UI and assignment surfaces

The navigation SHALL have a separate **SSH Keys** tab, showing profile names, server-side paths, public fingerprints, algorithms, and associated repositories, with register, rename, and remove actions. Add/Edit Repository SHALL have an SSH authentication selector containing System SSH and valid registered profiles for eligible SSH Git remotes. Repository cards SHALL identify System SSH or the assigned profile without exposing private paths.

The UI SHALL explain server-side path resolution, agent inheritance, encrypted-key unlocking outside the app, and local-only deployment. Invalid, unavailable, or forbidden profile operations SHALL remain visible failures with controls restored; inability to load profiles SHALL not silently clear an existing association or switch the selector to System SSH. Protected metadata SHALL be rendered as text, and valid theme/search-scope selections SHALL be preserved.

CLI add/update SHALL accept `--ssh-key <key_id>`; update SHALL also support mutually exclusive `--system-ssh` for explicit clearing. MCP update SHALL accept optional nullable `ssh_key_id`. Omitted values SHALL preserve existing update behavior. Both transports' repository lists SHALL expose only additive nullable `ssh_key_id`, not private profile paths. This change SHALL not introduce MCP key-provisioning tools.

#### Scenario: Choose a key in Add/Edit
- **WHEN** a user selects a registered profile for an eligible SSH remote
- **THEN** the submitted configuration includes its key ID and applies it before automatic Git work
- **AND** profile-load errors do not silently submit a null replacement

#### Scenario: Clear through CLI or MCP
- **WHEN** CLI update uses `--system-ssh` or MCP update supplies `ssh_key_id: null`
- **THEN** the repository becomes unassigned
- **AND** subsequent default operations use inherited system SSH within the deployment boundary
