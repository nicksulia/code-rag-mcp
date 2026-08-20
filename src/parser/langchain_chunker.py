"""
LangChain-based multi-language code parser and chunker.
Implements recursive syntactic splitting matching LangChain Language separator hierarchies,
enriched context injection, accurate line tracking, and symbol detection.
"""

import re
import hashlib
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from ..models.schema import CodeChunk, ChunkType


class SupportedLanguage(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    CPP = "cpp"
    C = "c"
    CSHARP = "csharp"
    RUBY = "ruby"
    PHP = "php"
    MARKDOWN = "markdown"
    HTML = "html"
    SQL = "sql"
    SOLIDITY = "solidity"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    BASH = "bash"
    TEXT = "text"


LANGUAGE_EXTENSIONS: Dict[str, str] = {
    ".py": SupportedLanguage.PYTHON.value,
    ".pyi": SupportedLanguage.PYTHON.value,
    ".ts": SupportedLanguage.TYPESCRIPT.value,
    ".tsx": SupportedLanguage.TYPESCRIPT.value,
    ".js": SupportedLanguage.JAVASCRIPT.value,
    ".jsx": SupportedLanguage.JAVASCRIPT.value,
    ".mjs": SupportedLanguage.JAVASCRIPT.value,
    ".cjs": SupportedLanguage.JAVASCRIPT.value,
    ".go": SupportedLanguage.GO.value,
    ".rs": SupportedLanguage.RUST.value,
    ".java": SupportedLanguage.JAVA.value,
    ".cpp": SupportedLanguage.CPP.value,
    ".cc": SupportedLanguage.CPP.value,
    ".cxx": SupportedLanguage.CPP.value,
    ".hpp": SupportedLanguage.CPP.value,
    ".hxx": SupportedLanguage.CPP.value,
    ".c": SupportedLanguage.C.value,
    ".h": SupportedLanguage.C.value,
    ".cs": SupportedLanguage.CSHARP.value,
    ".rb": SupportedLanguage.RUBY.value,
    ".php": SupportedLanguage.PHP.value,
    ".md": SupportedLanguage.MARKDOWN.value,
    ".markdown": SupportedLanguage.MARKDOWN.value,
    ".html": SupportedLanguage.HTML.value,
    ".htm": SupportedLanguage.HTML.value,
    ".sql": SupportedLanguage.SQL.value,
    ".sol": SupportedLanguage.SOLIDITY.value,
    ".json": SupportedLanguage.JSON.value,
    ".yaml": SupportedLanguage.YAML.value,
    ".yml": SupportedLanguage.YAML.value,
    ".toml": SupportedLanguage.TOML.value,
    ".sh": SupportedLanguage.BASH.value,
    ".bash": SupportedLanguage.BASH.value,
    ".zsh": SupportedLanguage.BASH.value,
}

# LangChain canonical separator hierarchies per language
LANGCHAIN_SEPARATORS: Dict[str, List[str]] = {
    SupportedLanguage.PYTHON.value: [
        "\nclass ",
        "\ndef ",
        "\nasync def ",
        "\n    def ",
        "\n    async def ",
        "\n\tdef ",
        "\n\tasync def ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.TYPESCRIPT.value: [
        "\nexport interface ",
        "\ninterface ",
        "\nexport class ",
        "\nclass ",
        "\nexport function ",
        "\nexport async function ",
        "\nfunction ",
        "\nasync function ",
        "\nexport const ",
        "\nconst ",
        "\nexport enum ",
        "\nenum ",
        "\nexport type ",
        "\ntype ",
        "\nnamespace ",
        "\nexport ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.JAVASCRIPT.value: [
        "\nexport class ",
        "\nclass ",
        "\nexport function ",
        "\nexport async function ",
        "\nfunction ",
        "\nasync function ",
        "\nexport const ",
        "\nconst ",
        "\nexport let ",
        "\nexport var ",
        "\nexport ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.GO.value: [
        "\nfunc ",
        "\ntype ",
        "\npackage ",
        "\nimport ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.RUST.value: [
        "\npub async fn ",
        "\nasync fn ",
        "\npub fn ",
        "\nfn ",
        "\npub struct ",
        "\nstruct ",
        "\npub enum ",
        "\nenum ",
        "\npub trait ",
        "\ntrait ",
        "\nimpl ",
        "\npub mod ",
        "\nmod ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.JAVA.value: [
        "\npublic class ",
        "\nclass ",
        "\npublic interface ",
        "\ninterface ",
        "\nenum ",
        "\npublic ",
        "\nprotected ",
        "\nprivate ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.CPP.value: [
        "\nclass ",
        "\nstruct ",
        "\nenum ",
        "\nnamespace ",
        "\ntemplate",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.C.value: [
        "\nstruct ",
        "\nenum ",
        "\ntypedef ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.CSHARP.value: [
        "\npublic class ",
        "\nclass ",
        "\npublic interface ",
        "\ninterface ",
        "\nenum ",
        "\nstruct ",
        "\nnamespace ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.RUBY.value: [
        "\ndef ",
        "\nclass ",
        "\nmodule ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.PHP.value: [
        "\nfunction ",
        "\nclass ",
        "\ninterface ",
        "\ntrait ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.MARKDOWN.value: [
        "\n# ",
        "\n## ",
        "\n### ",
        "\n#### ",
        "\n##### ",
        "\n###### ",
        "```\n",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.HTML.value: [
        "<body",
        "<main",
        "<header",
        "<footer",
        "<section",
        "<article",
        "<div",
        "<p",
        "<br",
        "<li",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.SQL.value: [
        "\nCREATE TABLE ",
        "\nCREATE ",
        "\nSELECT ",
        "\nINSERT ",
        "\nUPDATE ",
        "\nDELETE ",
        "\nALTER ",
        "\nDROP ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
    SupportedLanguage.SOLIDITY.value: [
        "\ncontract ",
        "\ninterface ",
        "\nlibrary ",
        "\nfunction ",
        "\nstruct ",
        "\nenum ",
        "\npragma ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
}

DEFAULT_SEPARATORS: List[str] = ["\n\n", "\n", " ", ""]


class RecursiveCharacterTextSplitterCore:
    """
    Core implementation of LangChain's RecursiveCharacterTextSplitter.
    Recursively splits text into chunks along language-specific separator hierarchies.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        separators: Optional[List[str]] = None,
        keep_separator: bool = True,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or DEFAULT_SEPARATORS
        self.keep_separator = keep_separator

    def split_text(self, text: str) -> List[str]:
        return self._split_text(text, self.separators)

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        final_chunks: List[str] = []
        separator = separators[-1]
        new_separators: List[str] = []

        for i, _s in enumerate(separators):
            _separator = _s
            if _separator == "":
                separator = _separator
                break
            if _separator in text:
                separator = _separator
                new_separators = separators[i + 1 :]
                break

        splits = self._split_on_separator(text, separator)

        good_splits: List[str] = []
        _separator_join = "" if self.keep_separator else separator

        for s in splits:
            if len(s) < self.chunk_size:
                good_splits.append(s)
            else:
                if good_splits:
                    merged = self._merge_splits(good_splits, _separator_join)
                    final_chunks.extend(merged)
                    good_splits = []
                if not new_separators:
                    final_chunks.append(s)
                else:
                    other_info = self._split_text(s, new_separators)
                    final_chunks.extend(other_info)

        if good_splits:
            merged = self._merge_splits(good_splits, _separator_join)
            final_chunks.extend(merged)

        return final_chunks

    def _split_on_separator(self, text: str, separator: str) -> List[str]:
        if separator:
            if self.keep_separator:
                parts = text.split(separator)
                result = []
                for idx, p in enumerate(parts):
                    if idx == 0:
                        if p:
                            result.append(p)
                    else:
                        result.append(separator + p)
                return result
            else:
                return [p for p in text.split(separator) if p]
        else:
            return list(text)

    def _merge_splits(self, splits: List[str], separator: str) -> List[str]:
        docs: List[str] = []
        current_doc: List[str] = []
        total = 0

        for d in splits:
            _len = len(d)
            if total + _len + (len(separator) if current_doc else 0) > self.chunk_size:
                if total > 0:
                    doc = separator.join(current_doc).strip()
                    if doc:
                        docs.append(doc)
                    while total > self.chunk_overlap or (
                        total + _len + (len(separator) if current_doc else 0)
                        > self.chunk_size
                        and total > 0
                    ):
                        total -= len(current_doc[0]) + (
                            len(separator) if len(current_doc) > 1 else 0
                        )
                        current_doc = current_doc[1:]
                        if not current_doc:
                            break
            current_doc.append(d)
            total += _len + (len(separator) if len(current_doc) > 1 else 0)

        doc = separator.join(current_doc).strip()
        if doc:
            docs.append(doc)

        return docs


class LangChainCodeChunker:
    """
    LangChain-based Code Chunker.
    Splits multi-repo source code files using LangChain recursive language hierarchies,
    captures top-level constructs as semantic units, performs line coordinate mapping,
    and attaches rich context headers.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        min_chunk_lines: int = 2,
        max_chunk_lines: int = 150,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_lines = min_chunk_lines
        self.max_chunk_lines = max_chunk_lines

    def detect_language(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        return LANGUAGE_EXTENSIONS.get(ext, "text")

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
        docstring: Optional[str] = None,
    ) -> str:
        header_lines = [
            f"// [Context] Repository: {repo_id} | File: {file_path} | Language: {language}"
        ]
        if parent_symbol and symbol_name:
            header_lines.append(
                f"// Scope: {parent_symbol} -> {symbol_name} ({chunk_type.value})"
            )
        elif symbol_name:
            header_lines.append(f"// Symbol: {symbol_name} ({chunk_type.value})")
        elif chunk_type != ChunkType.BLOCK:
            header_lines.append(f"// Type: {chunk_type.value}")

        if imports:
            clean_imports = [imp.strip() for imp in imports[:4] if imp.strip()]
            if clean_imports:
                header_lines.append(f"// Top Imports: {', '.join(clean_imports)}")

        if docstring:
            clean_doc = docstring.strip().replace("\n", " ")[:150]
            header_lines.append(f"// Doc: {clean_doc}")

        return "\n".join(header_lines)

    def _extract_file_imports(self, content: str, language: str) -> List[str]:
        imports: List[str] = []
        lines = content.splitlines()

        if language == "python":
            for line in lines[:60]:
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports.append(stripped)
        elif language in ("typescript", "javascript"):
            import_pattern = re.compile(
                r'import\s+(?:(?:{[^}]+})|(?:[\w\s,*]+))\s+from\s+[\'"]([^\'"]+)[\'"]'
            )
            for line in lines[:60]:
                if import_pattern.search(line) or line.strip().startswith("import "):
                    imports.append(line.strip())
        elif language == "go":
            for line in lines[:40]:
                if line.strip().startswith("import ") or line.strip().startswith('"'):
                    imports.append(line.strip())
        elif language in ("rust", "c", "cpp"):
            for line in lines[:40]:
                if line.strip().startswith("use ") or line.strip().startswith(
                    "#include"
                ):
                    imports.append(line.strip())

        return imports[:6]

    def _detect_symbol_and_type(
        self, chunk_text: str, language: str
    ) -> Tuple[Optional[str], ChunkType, Optional[str], Optional[str]]:
        lines = [line for line in chunk_text.splitlines() if line.strip()]
        if not lines:
            return None, ChunkType.BLOCK, None, None

        docstring: Optional[str] = None
        m_doc = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', chunk_text, re.DOTALL)
        if m_doc:
            docstring = (m_doc.group(1) or m_doc.group(2) or "").strip()

        if language == "python":
            for line in lines:
                sline = line.strip()
                m_cls = re.match(r"^class\s+([A-Za-z0-9_]+)", sline)
                if m_cls:
                    return m_cls.group(1), ChunkType.CLASS, None, docstring
                m_fn = re.match(r"^(?:async\s+)?def\s+([A-Za-z0-9_]+)", sline)
                if m_fn:
                    c_type = (
                        ChunkType.METHOD
                        if line.startswith("    ") or line.startswith("\t")
                        else ChunkType.FUNCTION
                    )
                    return m_fn.group(1), c_type, None, docstring

        elif language in ("typescript", "javascript"):
            for line in lines:
                sline = line.strip()
                m = re.search(
                    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(class|interface|type|enum|function)\s+([A-Za-z0-9_$]+)",
                    sline,
                )
                if m:
                    kind, name = m.group(1), m.group(2)
                    if kind == "class":
                        return name, ChunkType.CLASS, None, docstring
                    elif kind == "interface":
                        return name, ChunkType.INTERFACE, None, docstring
                    elif kind in ("type", "enum"):
                        return name, ChunkType.STRUCT, None, docstring
                    elif kind == "function":
                        return name, ChunkType.FUNCTION, None, docstring

                m_arrow = re.search(
                    r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>",
                    sline,
                )
                if m_arrow:
                    return m_arrow.group(1), ChunkType.FUNCTION, None, docstring

        elif language == "go":
            for line in lines:
                sline = line.strip()
                m_fn = re.match(r"^func\s+(?:\((?:[^)]+)\)\s+)?([A-Za-z0-9_]+)", sline)
                if m_fn:
                    return m_fn.group(1), ChunkType.FUNCTION, None, docstring
                m_typ = re.match(r"^type\s+([A-Za-z0-9_]+)\s+(struct|interface)", sline)
                if m_typ:
                    c_type = (
                        ChunkType.STRUCT
                        if m_typ.group(2) == "struct"
                        else ChunkType.INTERFACE
                    )
                    return m_typ.group(1), c_type, None, docstring

        elif language == "rust":
            for line in lines:
                sline = line.strip()
                m = re.match(
                    r"^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?(fn|struct|enum|trait|impl)\s+([A-Za-z0-9_]+)",
                    sline,
                )
                if m:
                    kind, name = m.group(1), m.group(2)
                    c_type = ChunkType.FUNCTION if kind == "fn" else ChunkType.STRUCT
                    return name, c_type, None, docstring

        elif language == "markdown":
            for line in lines:
                m_hd = re.match(r"^#{1,6}\s+(.+)$", line.strip())
                if m_hd:
                    return m_hd.group(1).strip(), ChunkType.DOC, None, docstring

        return None, ChunkType.BLOCK, None, docstring

    def _split_into_primary_sections(self, content: str, language: str) -> List[str]:
        """
        Splits content along LangChain primary construct boundaries before recursive splitting.
        """
        separators = LANGCHAIN_SEPARATORS.get(language, DEFAULT_SEPARATORS)

        # Determine primary delimiter patterns for language
        if language == "python":
            pattern = re.compile(r"(?=\n(?:class|def|async def)\s+)")
        elif language in ("typescript", "javascript"):
            pattern = re.compile(
                r"(?=\n(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:class|interface|type|enum|function|const|let|var)\s+)"
            )
        elif language == "go":
            pattern = re.compile(r"(?=\n(?:func|type|package)\s+)")
        elif language == "rust":
            pattern = re.compile(
                r"(?=\n(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|mod)\s+)"
            )
        elif language == "markdown":
            pattern = re.compile(r"(?=\n#{1,6}\s+)")
        elif language == "sql":
            pattern = re.compile(
                r"(?=\n(?:CREATE|SELECT|INSERT|UPDATE|DELETE|ALTER|DROP)\s+)",
                re.IGNORECASE,
            )
        else:
            pattern = re.compile(r"(?=\n\n)")

        raw_sections = pattern.split(content)
        sections = [s for s in raw_sections if s.strip()]
        if not sections:
            return [content] if content.strip() else []

        # If a single section is larger than chunk_size, recursively subdivide it
        splitter = RecursiveCharacterTextSplitterCore(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=separators,
            keep_separator=True,
        )

        final_sections: List[str] = []
        for sec in sections:
            if len(sec) > self.chunk_size:
                sub_splits = splitter.split_text(sec)
                final_sections.extend(sub_splits)
            else:
                final_sections.append(sec)

        return final_sections

    def chunk_file(self, repo_id: str, file_path: str, content: str) -> List[CodeChunk]:
        """
        Splits source file into LangChain chunks with line coordinates and context headers.
        """
        language = self.detect_language(file_path)
        if not content.strip():
            return []

        raw_sections = self._split_into_primary_sections(content, language)
        if not raw_sections:
            return []

        file_imports = self._extract_file_imports(content, language)
        chunks: List[CodeChunk] = []

        cursor = 0
        for split in raw_sections:
            clean_split = split.strip()
            if not clean_split:
                continue

            # Find chunk start position
            start_pos = content.find(split, cursor)
            if start_pos == -1:
                start_pos = content.find(clean_split, cursor)
            if start_pos == -1:
                start_pos = cursor

            cursor = max(cursor, start_pos + 1)

            # Accurate 1-indexed line calculations
            start_line = content[:start_pos].count("\n") + 1
            chunk_lines = split.count("\n")
            end_line = max(start_line, start_line + chunk_lines)

            symbol_name, chunk_type, parent_symbol, docstring = (
                self._detect_symbol_and_type(clean_split, language)
            )

            # In Python, if top of file has class but inside has method
            if language == "python" and "class " in content[:start_pos]:
                # Locate enclosing class
                prev_lines = content[:start_pos].splitlines()
                for pl in reversed(prev_lines):
                    m_parent = re.match(r"^class\s+([A-Za-z0-9_]+)", pl.strip())
                    if m_parent:
                        if chunk_type == ChunkType.FUNCTION:
                            chunk_type = ChunkType.METHOD
                            parent_symbol = m_parent.group(1)
                        break

            header = self._build_context_header(
                repo_id=repo_id,
                file_path=file_path,
                language=language,
                symbol_name=symbol_name,
                chunk_type=chunk_type,
                parent_symbol=parent_symbol,
                imports=file_imports,
                docstring=docstring,
            )
            enriched = f"{header}\n{clean_split}"

            chunk_id = self._make_chunk_id(repo_id, file_path, start_line, end_line)

            chunks.append(
                CodeChunk(
                    chunk_id=chunk_id,
                    repo_id=repo_id,
                    file_path=file_path,
                    language=language,
                    start_line=start_line,
                    end_line=end_line,
                    raw_content=clean_split,
                    enriched_content=enriched,
                    symbol_name=symbol_name,
                    chunk_type=chunk_type,
                    parent_symbol=parent_symbol,
                    imports=file_imports[:5],
                    docstring=docstring,
                )
            )

        return chunks


# Compatibility aliases
ASTChunker = LangChainCodeChunker
LangChainChunker = LangChainCodeChunker
CodeChunker = LangChainCodeChunker
