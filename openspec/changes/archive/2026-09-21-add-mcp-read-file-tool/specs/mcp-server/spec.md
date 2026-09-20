## ADDED Requirements

### Requirement: `read_file` Tool

The MCP server SHALL expose a `read_file` tool that returns the full text content of a single file from a registered repository, equivalent to the REST endpoint `GET /api/v1/file`.

Arguments:

- `repo_id` (`string`, **required**): the unique repository ID containing the file (e.g. `'update-api'`).
- `path` (`string`, **required**): the file path relative to the repository root (e.g. `'src/polling/usecase.py'`).

On success, the tool SHALL return the repository ID, the requested file path, and the file's full text content. Missing or empty arguments SHALL be rejected with a structured tool error naming the missing argument. When the repository is not registered, or the path does not resolve to an existing file inside the repository root, the tool SHALL return a structured "not found" tool error and SHALL NOT leak filesystem paths outside the repository root.

#### Scenario: Reading an existing file through MCP

- **WHEN** the tool is called with `repo_id: "update-api"` and `path: "src/polling/usecase.py"`, and that file exists in the repository
- **THEN** the tool returns the repository ID, the file path, and the file's full text content

#### Scenario: Missing required argument rejected

- **WHEN** the tool is called without `repo_id` or without `path`
- **THEN** the tool returns a structured error naming the missing argument
- **AND** no file is read

#### Scenario: Unknown repository rejected

- **WHEN** the tool is called with a `repo_id` that is not registered
- **THEN** the tool returns a structured "not found" error
- **AND** no file content is returned

#### Scenario: Nonexistent file rejected

- **WHEN** the tool is called for a registered repository with a `path` that does not exist as a file in that repository
- **THEN** the tool returns a structured "not found" error
- **AND** no file content is returned

#### Scenario: Path escape rejected

- **WHEN** the tool is called with a `path` that escapes the repository root (e.g. `'../../etc/passwd'`) or is an absolute filesystem path
- **THEN** the tool returns a structured "not found" error
- **AND** no file content is returned
- **AND** no filesystem path outside the repository root is leaked
