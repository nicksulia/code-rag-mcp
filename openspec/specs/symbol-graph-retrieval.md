# Specification: Symbol Graph & Cross-Repository Dependency Retrieval

## Status: ACTIVE
## Domain: Graph Retrieval

---

## 1. Overview
Codebases are deeply interconnected networks of functions, types, and cross-repo API calls. The Symbol Graph tracks definitions, references, call relationships, and schema contracts across repositories. When a function or interface is retrieved, the graph engine expands the context to include callers, callees, definitions, and dependent contracts.

---

## 2. Requirements

### 2.1 Symbol Extraction & Graph Structure
- Nodes:
  - `Symbol`: `id`, `repo_id`, `name`, `kind` (`function`, `class`, `interface`, `variable`, `api_endpoint`, `rpc_service`), `file_path`, `line_number`, `signature`.
- Edges:
  - `CALLS`: Function A calls Function B.
  - `DEFINES`: Class / File defines Symbol.
  - `IMPORTS`: File A imports Symbol from File/Package B.
  - `IMPLEMENTS` / `INHERITS`: Class A implements Interface B.
  - `CROSS_REPO_API`: Client endpoint in Repo A calls REST/gRPC endpoint in Repo B.

### 2.2 Graph Neighborhood Querying
- Given a candidate chunk with symbol `S`, the retriever can fetch:
  - 1-hop callers (where is this used?).
  - 1-hop callees (what does this rely on?).
  - Type definition / schema for parameters and return types.
- Context injection: Inject condensed 1-line signatures of caller/callee relationships into the retrieval prompt to provide structural clarity without overwhelming token budgets.
