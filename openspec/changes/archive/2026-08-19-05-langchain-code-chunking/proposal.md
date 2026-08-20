# Change Proposal: LangChain-Based Code Chunking

- **Change ID**: `05-langchain-code-chunking`
- **Author**: Antigravity Engineering
- **Status**: COMPLETED
- **Created**: 2026-08-17

---

## 1. Why (Motivation)
The legacy custom AST/regex-based chunker had varying custom parsing rules across languages and lacked standard delimiter recursion for complex, deeply nested code files. By standardizing on **LangChain's Recursive Code Splitting model** (`RecursiveCharacterTextSplitter.from_language`), we gain:
1. **Broader & Standardized Multi-Language Support**: Official language separator hierarchies for Python, TypeScript, JavaScript, Go, Rust, Java, C++, C#, Ruby, PHP, Markdown, HTML, SQL, Solidity, and fallback text.
2. **Context Preservation**: Retaining syntactic function, class, and block boundaries without brittle AST parser failures on syntax variations.
3. **Consistent Chunk Sizing**: Clean token/character budgeting with configurable target size, overlap, and enriched context headers.
4. **Resilience**: Zero-crash parsing fallback, accurate 1-indexed line number tracking, and seamless integration with existing vector, BM25, and graph indexing stores.

---

## 2. Scope & Goals

### In Scope
- [x] Create specification `openspec/specs/langchain-code-chunking.md` and mark `ast-code-chunking.md` as superseded.
- [x] Implement `LangChainCodeChunker` in `src/parser/langchain_chunker.py` with multi-language splitting, delimiter hierarchy, symbol detection, context headers, and line range resolution.
- [x] Update `requirements.txt` with `langchain-text-splitters` and `langchain`.
- [x] Export `LangChainCodeChunker` and provide backwards-compatible alias `ASTChunker` in `src/parser/__init__.py`.
- [x] Integrate `LangChainCodeChunker` in `MultiRepoRAGService` (`src/service.py`).
- [x] Update CLI stats and UI tooltip indicators.
- [x] Add comprehensive unit tests in `tests/test_langchain_chunker.py` and verify legacy suite passes.

---

## 3. Impact Analysis
- **Indexing & Retrieval**: All indexed chunks now use LangChain recursive language boundaries while preserving the exact `CodeChunk` schema (`chunk_id`, `repo_id`, `file_path`, `language`, `start_line`, `end_line`, `symbol_name`, `chunk_type`, `enriched_content`).
- **Backward Compatibility**: `ASTChunker` remains as a compatibility alias pointing to `LangChainCodeChunker` so that existing scripts and external plugins do not break.
- **Graph & Vector Stores**: Zero schema alterations needed for VectorStore, BM25LexicalStore, or GraphStore.
