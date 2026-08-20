# Specification: LangChain-Based Code Parsing & Chunking

## Status: ACTIVE
## Domain: Code Intelligence
## Supersedes: ast-code-chunking.md

---

## 1. Overview
The LangChain Code Parser splits source code files into semantically and structurally coherent chunks using LangChain's multi-language recursive character text splitting strategy (`RecursiveCharacterTextSplitter.from_language`). It preserves syntactic construct boundaries across programming languages, handles deeply nested code gracefully, and enriches every chunk with deterministic line coordinates and contextual scope headers.

---

## 2. Requirements & Scenarios

### Requirement: Multi-Language Recursive Chunking
The parser SHALL support language-specific delimiter splitting for all primary repository languages:
- **Python**: Splitting on class definitions, function definitions, method indentations, docstrings, and newlines.
- **TypeScript / JavaScript**: Splitting on exports, classes, interfaces, types, enums, functions, and arrow declarations.
- **Go**: Splitting on package declarations, imports, types, structs, interfaces, and functions.
- **Rust**: Splitting on module declarations, traits, impls, structs, enums, and functions.
- **Java / C++ / C# / C**: Splitting on classes, namespaces, structs, interfaces, and method signatures.
- **Markdown / HTML / SQL**: Splitting on structural headers, HTML tags, and SQL queries.
- **Fallback / Config**: Recursive fallback splitting on paragraphs, lines, and words.

#### Scenario: Splitting Python source files with classes and standalone functions
- **WHEN** a Python file with a class, methods, and module-level functions is processed
- **THEN** chunks are partitioned along syntactic boundaries, identifying function and class symbols without syntax tree crashing.

#### Scenario: Splitting TypeScript interfaces and async functions
- **WHEN** a TypeScript file containing interfaces and async exported functions is parsed
- **THEN** chunks capture interface definitions and functions with matching `ChunkType.INTERFACE` and `ChunkType.FUNCTION` metadata.

---

### Requirement: Context Header and Scope Injection
Each chunk SHALL be prepended with a structured metadata context header:
1. File and repository location: `// [Context] Repository: <repo_id> | File: <file_path> | Language: <language>`
2. Symbol identification: `// Symbol: <symbol_name> (<chunk_type>)` or `// Scope: <parent> -> <symbol>`
3. Top detected file imports or package context.

#### Scenario: Chunk context enrichment
- **WHEN** any source file is chunked
- **THEN** `raw_content` contains the exact slice of code and `enriched_content` contains the metadata header followed by `raw_content`.

---

### Requirement: Accurate Line Number and Identifier Tracking
Every chunk SHALL record 1-indexed `start_line` and `end_line` coordinates relative to the original source file, alongside a deterministic 16-character SHA-256 hash `chunk_id`.

#### Scenario: Line coordinate precision
- **WHEN** a chunk is generated from lines 25 to 55 of a source file
- **THEN** `start_line` equals 25, `end_line` equals 55, and `chunk_id` is reproducible across indexing runs.

---

### Requirement: Backward Compatibility
The module SHALL provide seamless drop-in backwards compatibility with the previous `ASTChunker` interface.

#### Scenario: Legacy ASTChunker instantiation
- **WHEN** client code imports `from src.parser import ASTChunker` and invokes `chunk_file(...)`
- **THEN** it executes the LangChain-based chunking pipeline without errors.
