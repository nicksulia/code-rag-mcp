"""
AST-aware semantic code parser and chunker.
Extracts functions, classes, interfaces, methods, and blocks with enclosing context and imports.
"""

import ast
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from ..models.schema import CodeChunk, ChunkType


LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}


class ASTChunker:
    def __init__(self, min_chunk_lines: int = 4, max_chunk_lines: int = 150):
        self.min_chunk_lines = min_chunk_lines
        self.max_chunk_lines = max_chunk_lines

    def detect_language(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        return LANGUAGE_EXTENSIONS.get(ext, "text")

    def chunk_file(self, repo_id: str, file_path: str, content: str) -> List[CodeChunk]:
        language = self.detect_language(file_path)
        lines = content.splitlines()
        if not lines:
            return []

        if language == "python":
            chunks = self._chunk_python(repo_id, file_path, content, lines)
        elif language in ("typescript", "javascript"):
            chunks = self._chunk_js_ts(repo_id, file_path, content, lines, language)
        elif language == "go":
            chunks = self._chunk_go(repo_id, file_path, content, lines)
        elif language == "rust":
            chunks = self._chunk_rust(repo_id, file_path, content, lines)
        elif language == "markdown":
            chunks = self._chunk_markdown(repo_id, file_path, content, lines)
        else:
            chunks = self._chunk_generic_code(
                repo_id, file_path, content, lines, language
            )

        if not chunks:
            # Fallback single block or sliding window
            chunks = self._chunk_sliding_window(repo_id, file_path, lines, language)

        return chunks

    def _make_chunk_id(
        self, repo_id: str, file_path: str, start_line: int, end_line: int
    ) -> str:
        raw = f"{repo_id}:{file_path}:{start_line}:{end_line}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _build_context_header(
        self,
        repo_id: str,
        file_path: str,
        language: str,
        symbol_name: Optional[str],
        chunk_type: ChunkType,
        parent_symbol: Optional[str],
        imports: List[str],
        docstring: Optional[str],
    ) -> str:
        header_lines = [
            f"// [Context] Repository: {repo_id} | File: {file_path} | Language: {language}"
        ]
        if parent_symbol:
            header_lines.append(
                f"// Scope: {parent_symbol} -> {symbol_name or 'block'} ({chunk_type.value})"
            )
        elif symbol_name:
            header_lines.append(f"// Symbol: {symbol_name} ({chunk_type.value})")

        if imports:
            clean_imports = [imp.strip() for imp in imports[:4] if imp.strip()]
            if clean_imports:
                header_lines.append(f"// Top Imports: {', '.join(clean_imports)}")

        if docstring:
            clean_doc = docstring.strip().replace("\n", " ")[:150]
            header_lines.append(f"// Doc: {clean_doc}")

        return "\n".join(header_lines)

    # -------------------------------------------------------------
    # Python AST Chunking
    # -------------------------------------------------------------
    def _chunk_python(
        self, repo_id: str, file_path: str, content: str, lines: List[str]
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        try:
            tree = ast.parse(content)
        except Exception:
            return self._chunk_generic_code(
                repo_id, file_path, content, lines, "python"
            )

        # Extract top-level imports
        imports: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = ", ".join(n.name for n in node.names)
                imports.append(f"from {mod} import {names}")

        def process_node(node: ast.AST, parent_name: Optional[str] = None):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l + len(node.body))
                raw_code = "\n".join(lines[start_l - 1 : end_l])
                doc = ast.get_docstring(node)
                c_type = ChunkType.METHOD if parent_name else ChunkType.FUNCTION

                header = self._build_context_header(
                    repo_id=repo_id,
                    file_path=file_path,
                    language="python",
                    symbol_name=node.name,
                    chunk_type=c_type,
                    parent_symbol=parent_name,
                    imports=imports,
                    docstring=doc,
                )
                enriched = f"{header}\n{raw_code}"

                chunks.append(
                    CodeChunk(
                        chunk_id=self._make_chunk_id(
                            repo_id, file_path, start_l, end_l
                        ),
                        repo_id=repo_id,
                        file_path=file_path,
                        language="python",
                        start_line=start_l,
                        end_line=end_l,
                        raw_content=raw_code,
                        enriched_content=enriched,
                        symbol_name=node.name,
                        chunk_type=c_type,
                        parent_symbol=parent_name,
                        imports=imports[:5],
                        docstring=doc,
                    )
                )

            elif isinstance(node, ast.ClassDef):
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l + len(node.body))
                raw_code = "\n".join(lines[start_l - 1 : end_l])
                doc = ast.get_docstring(node)

                # Class overview chunk
                class_header_end = min(start_l + 10, end_l)
                class_overview = "\n".join(lines[start_l - 1 : class_header_end])
                header = self._build_context_header(
                    repo_id=repo_id,
                    file_path=file_path,
                    language="python",
                    symbol_name=node.name,
                    chunk_type=ChunkType.CLASS,
                    parent_symbol=parent_name,
                    imports=imports,
                    docstring=doc,
                )
                enriched = f"{header}\n{class_overview}"

                chunks.append(
                    CodeChunk(
                        chunk_id=self._make_chunk_id(
                            repo_id, file_path, start_l, class_header_end
                        ),
                        repo_id=repo_id,
                        file_path=file_path,
                        language="python",
                        start_line=start_l,
                        end_line=class_header_end,
                        raw_content=class_overview,
                        enriched_content=enriched,
                        symbol_name=node.name,
                        chunk_type=ChunkType.CLASS,
                        parent_symbol=parent_name,
                        imports=imports[:5],
                        docstring=doc,
                    )
                )

                # Process child methods
                for sub_node in node.body:
                    process_node(sub_node, parent_name=node.name)

        for top_node in tree.body:
            process_node(top_node)

        return chunks

    # -------------------------------------------------------------
    # TypeScript / JavaScript Chunking
    # -------------------------------------------------------------
    def _chunk_js_ts(
        self,
        repo_id: str,
        file_path: str,
        content: str,
        lines: List[str],
        language: str,
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        imports: List[str] = []

        import_pattern = re.compile(
            r'import\s+(?:(?:{[^}]+})|(?:[\w\s,*]+))\s+from\s+[\'"]([^\'"]+)[\'"]'
        )
        for line in lines:
            m = import_pattern.search(line)
            if m:
                imports.append(line.strip())

        # Patterns for functions, classes, interfaces, types, exports
        func_class_pattern = re.compile(
            r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class|interface|type|enum|const|let|var)\s+([A-Za-z0-9_$]+)"
        )

        i = 0
        total_lines = len(lines)
        while i < total_lines:
            line = lines[i]
            match = func_class_pattern.match(line.strip())
            if match:
                symbol = match.group(1)
                start_l = i + 1
                c_type = ChunkType.FUNCTION
                if "class " in line:
                    c_type = ChunkType.CLASS
                elif "interface " in line:
                    c_type = ChunkType.INTERFACE
                elif "type " in line or "enum " in line:
                    c_type = ChunkType.STRUCT

                # Find block boundary via brace counting
                end_l = self._find_block_end(lines, i)
                raw_code = "\n".join(lines[start_l - 1 : end_l])

                header = self._build_context_header(
                    repo_id=repo_id,
                    file_path=file_path,
                    language=language,
                    symbol_name=symbol,
                    chunk_type=c_type,
                    parent_symbol=None,
                    imports=imports,
                    docstring=None,
                )
                enriched = f"{header}\n{raw_code}"

                chunks.append(
                    CodeChunk(
                        chunk_id=self._make_chunk_id(
                            repo_id, file_path, start_l, end_l
                        ),
                        repo_id=repo_id,
                        file_path=file_path,
                        language=language,
                        start_line=start_l,
                        end_line=end_l,
                        raw_content=raw_code,
                        enriched_content=enriched,
                        symbol_name=symbol,
                        chunk_type=c_type,
                        parent_symbol=None,
                        imports=imports[:5],
                    )
                )
                i = max(i + 1, end_l)
            else:
                i += 1

        return chunks

    # -------------------------------------------------------------
    # Go Chunking
    # -------------------------------------------------------------
    def _chunk_go(
        self, repo_id: str, file_path: str, content: str, lines: List[str]
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        package_name = ""
        imports: List[str] = []

        pkg_match = re.search(r"^package\s+([A-Za-z0-9_]+)", content, re.MULTILINE)
        if pkg_match:
            package_name = pkg_match.group(1)

        go_pattern = re.compile(
            r"^func\s+(?:\((?:[^)]+)\)\s+)?([A-Za-z0-9_]+)|^type\s+([A-Za-z0-9_]+)\s+(struct|interface)"
        )

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = go_pattern.match(line)
            if match:
                symbol = match.group(1) or match.group(2)
                c_type = (
                    ChunkType.FUNCTION
                    if match.group(1)
                    else (
                        ChunkType.STRUCT
                        if match.group(3) == "struct"
                        else ChunkType.INTERFACE
                    )
                )
                start_l = i + 1
                end_l = self._find_block_end(lines, i)
                raw_code = "\n".join(lines[start_l - 1 : end_l])

                header = self._build_context_header(
                    repo_id=repo_id,
                    file_path=file_path,
                    language="go",
                    symbol_name=symbol,
                    chunk_type=c_type,
                    parent_symbol=f"package {package_name}" if package_name else None,
                    imports=imports,
                    docstring=None,
                )
                chunks.append(
                    CodeChunk(
                        chunk_id=self._make_chunk_id(
                            repo_id, file_path, start_l, end_l
                        ),
                        repo_id=repo_id,
                        file_path=file_path,
                        language="go",
                        start_line=start_l,
                        end_line=end_l,
                        raw_content=raw_code,
                        enriched_content=f"{header}\n{raw_code}",
                        symbol_name=symbol,
                        chunk_type=c_type,
                        parent_symbol=package_name,
                    )
                )
                i = max(i + 1, end_l)
            else:
                i += 1

        return chunks

    # -------------------------------------------------------------
    # Rust Chunking
    # -------------------------------------------------------------
    def _chunk_rust(
        self, repo_id: str, file_path: str, content: str, lines: List[str]
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        rust_pattern = re.compile(
            r"^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl)\s+([A-Za-z0-9_]+)"
        )

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = rust_pattern.match(line)
            if match:
                symbol = match.group(1)
                c_type = ChunkType.FUNCTION if "fn " in line else ChunkType.STRUCT
                start_l = i + 1
                end_l = self._find_block_end(lines, i)
                raw_code = "\n".join(lines[start_l - 1 : end_l])

                header = self._build_context_header(
                    repo_id=repo_id,
                    file_path=file_path,
                    language="rust",
                    symbol_name=symbol,
                    chunk_type=c_type,
                    parent_symbol=None,
                    imports=[],
                    docstring=None,
                )
                chunks.append(
                    CodeChunk(
                        chunk_id=self._make_chunk_id(
                            repo_id, file_path, start_l, end_l
                        ),
                        repo_id=repo_id,
                        file_path=file_path,
                        language="rust",
                        start_line=start_l,
                        end_line=end_l,
                        raw_content=raw_code,
                        enriched_content=f"{header}\n{raw_code}",
                        symbol_name=symbol,
                        chunk_type=c_type,
                    )
                )
                i = max(i + 1, end_l)
            else:
                i += 1

        return chunks

    # -------------------------------------------------------------
    # Markdown Chunking
    # -------------------------------------------------------------
    def _chunk_markdown(
        self, repo_id: str, file_path: str, content: str, lines: List[str]
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        heading_indices = []

        for idx, line in enumerate(lines):
            if re.match(r"^#{1,4}\s+(.+)$", line.strip()):
                heading_indices.append(idx)

        if not heading_indices:
            return self._chunk_sliding_window(repo_id, file_path, lines, "markdown")

        heading_indices.append(len(lines))
        for j in range(len(heading_indices) - 1):
            start_i = heading_indices[j]
            end_i = heading_indices[j + 1]
            start_l = start_i + 1
            end_l = end_i
            raw_code = "\n".join(lines[start_i:end_i]).strip()
            if not raw_code:
                continue

            heading_text = lines[start_i].strip("# ").strip()
            header = f"// [Doc Context] Repository: {repo_id} | Document: {file_path} | Section: {heading_text}"
            chunks.append(
                CodeChunk(
                    chunk_id=self._make_chunk_id(repo_id, file_path, start_l, end_l),
                    repo_id=repo_id,
                    file_path=file_path,
                    language="markdown",
                    start_line=start_l,
                    end_line=end_l,
                    raw_content=raw_code,
                    enriched_content=f"{header}\n{raw_code}",
                    symbol_name=heading_text,
                    chunk_type=ChunkType.DOC,
                )
            )

        return chunks

    # -------------------------------------------------------------
    # Generic / Fallback
    # -------------------------------------------------------------
    def _find_block_end(self, lines: List[str], start_idx: int) -> int:
        open_braces = 0
        found_first = False
        for k in range(start_idx, len(lines)):
            line = lines[k]
            open_braces += line.count("{") - line.count("}")
            if "{" in line:
                found_first = True
            if found_first and open_braces <= 0:
                return k + 1
            if k - start_idx > self.max_chunk_lines:
                return k + 1
        return min(start_idx + 40, len(lines))

    def _chunk_generic_code(
        self,
        repo_id: str,
        file_path: str,
        content: str,
        lines: List[str],
        language: str,
    ) -> List[CodeChunk]:
        return self._chunk_sliding_window(repo_id, file_path, lines, language)

    def _chunk_sliding_window(
        self,
        repo_id: str,
        file_path: str,
        lines: List[str],
        language: str,
        window_size: int = 60,
        step_size: int = 40,
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        total = len(lines)
        if total == 0:
            return []

        start = 0
        while start < total:
            end = min(start + window_size, total)
            start_l = start + 1
            end_l = end
            raw = "\n".join(lines[start:end])

            header = f"// [Context] Repository: {repo_id} | File: {file_path} | Lines: {start_l}-{end_l}"
            chunks.append(
                CodeChunk(
                    chunk_id=self._make_chunk_id(repo_id, file_path, start_l, end_l),
                    repo_id=repo_id,
                    file_path=file_path,
                    language=language,
                    start_line=start_l,
                    end_line=end_l,
                    raw_content=raw,
                    enriched_content=f"{header}\n{raw}",
                    chunk_type=ChunkType.BLOCK,
                )
            )
            if end >= total:
                break
            start += step_size

        return chunks
