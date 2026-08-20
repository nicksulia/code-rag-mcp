## MODIFIED Requirements

### Requirement: Allowed Hidden Directories & Skills Policy
While dot-prefixed directories are generally ignored, `.github/skills/**` (agent skill definitions `SKILL.md`, reference architectures, and skill assets) and `.github/workflows/**` (GitHub Actions workflow definition files, `*.yml`/`*.yaml`) MUST be traversed and indexed. All other non-allowed directories and files under `.github` (such as `.github/agents/`, `.github/prompts/`, `.github/instructions/`, `.github/memory.md`, and dotfiles like `.gitattributes`) MUST remain strictly ignored.

#### Scenario: Workflow YAML file is indexed
- **WHEN** the scanner encounters `.github/workflows/ci.yml` or `.github/workflows/release.yaml` in a repository
- **THEN** the file is treated as a regular indexable file (subject to standard size and gitignore rules) and is not excluded solely for being under `.github`

#### Scenario: Non-workflow, non-skills `.github` content stays ignored
- **WHEN** the scanner encounters `.github/agents/dev.agent.md`, `.github/prompts/plan.prompt.md`, `.github/instructions/python.instructions.md`, `.github/memory.md`, or `.github/.gitattributes`
- **THEN** the file is excluded from indexing

#### Scenario: Skills policy remains unaffected
- **WHEN** the scanner encounters `.github/skills/<name>/SKILL.md` or other files under `.github/skills/**`
- **THEN** the file continues to be traversed and indexed exactly as before this change

#### Scenario: Non-workflow files inside the workflows directory are still filtered by standard rules
- **WHEN** a file under `.github/workflows/**` matches a `.gitignore` pattern, exceeds the configured size guard, or has a default-ignored extension
- **THEN** the file is excluded, following the same standard filtering rules applied to any other allowed path
