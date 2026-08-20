# Specification: AST-Aware Code Parsing & Chunking

## Status: SUPERSEDED (superseded by langchain-code-chunking.md)
## Domain: Code Intelligence

---

## 1. Overview
The AST Code Parser splits source code files into semantically coherent chunks using Concrete/Abstract Syntax Trees (Tree-sitter) rather than naive character-count splitting. This preserves syntactic boundaries (functions, classes, methods, modules) and injects essential context (module headers, imports, class signatures, docstrings) into every chunk.

---

## 2. Requirements

### 2.1 Multi-Language Support
The parser MUST support AST parsing for key languages:
- **Python**: `def`, `async def`, `class`, module-level statements.
- **TypeScript / JavaScript / TSX / JSX**: `function`, `arrow function`, `class`, `interface`, `type`, `enum`, `export`.
- **Go**: `func`, `type struct`, `type interface`, `package`.
- **Rust**: `fn`, `struct`, `enum`, `impl`, `trait`, `mod`.
- **Java / C# / C++ / C**: `class`, `struct`, `interface`, `namespace`, methods.
- **Fallback Languages (HTML, CSS, SQL, YAML, JSON, Markdown)**: Block-based or structural paragraph chunking.

### 2.2 Semantic Chunking Rules
1. **Enclosing Scope Context**:
   - Each chunk MUST prepend scope context headers, including:
     - File path: `// File: <repo_id>/<relative_path>`
     - Enclosing class / struct / module: e.g. `class UserService:`
     - Relevant file imports (top imported libraries/modules).
2. **Chunk Size Bounds**:
   - Optimal target size: 200 - 800 tokens (approx. 50 - 150 lines of code).
   - If a function/method is smaller than the minimum threshold (e.g. 5 lines), merge with adjacent siblings in the same scope.
   - If a function or class exceeds maximum threshold (e.g. >1000 tokens), recursively partition by nested blocks or sub-statements while maintaining the function signature and header.
3. **Chunk Metadata**:
   - Every chunk MUST record:
     - `chunk_id`: Unique deterministic hash (repo + file + start_line + end_line).
     - `repo_id`: Owning repository ID.
     - `file_path`: Relative path inside repo.
     - `language`: Normalized language string.
     - `start_line` & `end_line` (1-indexed).
     - `symbol_name`: Primary identifier (e.g. `authenticate_user`).
     - `chunk_type`: `function` | `class` | `method` | `interface` | `module` | `doc` | `config`.
     - `parent_symbol`: Parent class or module name.
     - `raw_content`: The original verbatim code.
     - `enriched_content`: Code prepended with signature headers, imports, and metadata comments.
