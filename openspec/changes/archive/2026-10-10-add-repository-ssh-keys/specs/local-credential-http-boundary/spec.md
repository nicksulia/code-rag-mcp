# Spec Delta

## Purpose

Keep browser-accessible SSH credentials inside a trusted local-server boundary, reject hostile requests before they can change or use repository identities, and prevent HTTP/file-read routes from exposing private files.

## ADDED Requirements

### Requirement: HTTP SSH credential use is local-only

Key-management endpoints SHALL operate only on a verified loopback listener. A catalog containing any key profile SHALL cause non-loopback `serve` startup to fail explicitly before activating the listener. On non-loopback listeners without profiles, profile management and SSH Git network operations SHALL be forbidden, including unassigned repositories that could use an inherited agent. REST denials SHALL return `403`; HTTP MCP operation denials SHALL retain HTTP `200` JSON-RPC tool-error framing with `isError: true` and a safe forbidden-operation code. The policy SHALL survive effective Git URL rewrites to SSH.

Non-loopback HTTPS/local indexing and unrelated existing HTTP capabilities SHALL remain available under their existing deployment model. Direct trusted CLI and stdio MCP SHALL be permitted to use profiles without this HTTP restriction. Explicit local-only indexing SHALL not be treated as SSH network use. A configured MCP token SHALL not waive this boundary or be claimed to protect REST.

#### Scenario: Remote listener with existing profiles
- **WHEN** serve is requested on a non-loopback or wildcard address with profiles in the catalog
- **THEN** startup fails with safe local-only guidance before the listener is activated
- **AND** profiles and assignments remain unchanged

#### Scenario: Remote server attempts inherited-agent sync
- **WHEN** a non-loopback HTTP client requests SSH Git network preparation for an unassigned repository
- **THEN** the operation is denied before SSH execution
- **AND** the request cannot use the server's inherited agent, including through a Git URL rewrite

#### Scenario: Trusted direct operation
- **WHEN** a trusted local CLI or stdio MCP process synchronizes an assigned repository
- **THEN** it can use its selected profile and inherited matching agent under the repository SSH contract

### Requirement: Loopback requests validate Host and browser provenance

Every loopback HTTP request, including REST, `/mcp`, static files, and preflight requests, SHALL validate a single well-formed `Host` whose hostname is an accepted loopback address or localhost name and whose port matches the actual listener. Missing, malformed, duplicate, or unexpected Host values SHALL be rejected before dispatch.

Browser `Origin`, when present, SHALL exactly match the validated request origin's scheme, hostname, and actual port; malformed origins, opaque `null`, user information, paths, and multiple values SHALL be rejected. `Sec-Fetch-Site: cross-site` or `same-site` SHALL be rejected even if Origin is absent. A valid local non-browser request without Origin SHALL remain supported. Existing configured MCP bearer authentication SHALL still be enforced independently.

#### Scenario: DNS-rebinding Host
- **WHEN** a loopback request has `Host: attacker.example:<port>` even with an otherwise allowed or absent Origin
- **THEN** the request is rejected before key management, repository mutation, or tool execution

#### Scenario: Different local browser port
- **WHEN** a browser on `http://localhost:9000` requests the listener at `http://localhost:8000`
- **THEN** the mismatched origin is rejected despite both hostnames being localhost

#### Scenario: Normal local clients
- **WHEN** the dashboard sends a same-origin request or a local CLI sends a valid Host with no Origin
- **THEN** the request can proceed subject to its normal route authorization and validation

#### Scenario: Cross-site request without Origin
- **WHEN** a browser request declares cross-site fetch metadata but no Origin
- **THEN** it is rejected before operation dispatch rather than being treated as a trusted non-browser client

### Requirement: Loopback CORS does not expose credential-capable APIs

Loopback REST and key endpoints SHALL not emit wildcard `Access-Control-Allow-Origin`. Preflight responses SHALL follow the same Host/Origin checks as ordinary requests and SHALL not authorize arbitrary cross-origin sites. Same-origin dashboard access SHALL continue to work without browser token storage or a new authentication session. Non-loopback existing REST CORS behavior SHALL remain unchanged for otherwise permitted routes.

The boundary SHALL protect repository add/edit/sync and HTTP MCP calls as well as key endpoints; a hostile site SHALL not bypass profile protection by changing a remote or invoking a different sync surface.

#### Scenario: Hostile preflight or alternate mutation route
- **WHEN** a hostile browser origin preflights a key request or calls repository mutation or `/mcp` directly
- **THEN** origin checks reject the request and no route performs work
- **AND** the response does not authorize wildcard cross-origin access

### Requirement: Static and source file serving cannot expose private keys

Static-file serving SHALL resolve and contain requested files inside the configured web root, reject traversal/encoded traversal and symlinks escaping that root, and deny protected or recognized private-key files even inside the root. Such requests SHALL use a safe `404` without file contents or external-path details.

The same private-material protection SHALL apply to REST source-file and MCP `read_file` access. No key-management endpoint SHALL provide a private-key download. Catalog metadata access SHALL not be presented as an authenticated multi-user vault; other trusted processes owned by the server account remain outside this boundary.

#### Scenario: Static traversal reaches a key
- **WHEN** a request attempts to reach a private key outside the web root through `..`, encoded traversal, or a symlink
- **THEN** static serving returns safe not-found behavior without reading or returning the key

#### Scenario: Private file inside an allowed root
- **WHEN** a requested file inside the web/source root is a registered or recognized private key
- **THEN** file serving denies its contents even though ordinary root containment succeeds
