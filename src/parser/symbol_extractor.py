"""
Symbol and dependency extractor.
Extracts declared symbols, caller-callee call sites, imports, and cross-repo API signatures.
"""

import ast
import re
import hashlib
from typing import List, Dict, Tuple, Optional, Any
from ..models.schema import Symbol, CallEdge


class SymbolExtractor:
    def __init__(self):
        # Regex patterns for cross-repo API endpoints and client calls
        self.py_endpoint_pattern = re.compile(
            r'@(?:app|router|api)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']'
        )
        self.js_endpoint_pattern = re.compile(
            r'(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']'
        )
        self.client_call_pattern = re.compile(
            r'(?:axios|fetch|apiClient|http|client)\.(?:get|post|put|delete|patch|\b)\s*\(\s*["\']([^"\']+)["\']'
        )

    def extract_symbols_and_edges(
        self, repo_id: str, file_path: str, content: str, language: str
    ) -> Tuple[List[Symbol], List[CallEdge]]:
        symbols: List[Symbol] = []
        edges: List[CallEdge] = []

        if language == "python":
            s, e = self._extract_python(repo_id, file_path, content)
            symbols.extend(s)
            edges.extend(e)
        else:
            s, e = self._extract_regex_symbols(repo_id, file_path, content, language)
            symbols.extend(s)
            edges.extend(e)

        # Cross-repo API extraction
        api_symbols, api_edges = self._extract_api_signatures(
            repo_id, file_path, content
        )
        symbols.extend(api_symbols)
        edges.extend(api_edges)

        return symbols, edges

    def _extract_python(
        self, repo_id: str, file_path: str, content: str
    ) -> Tuple[List[Symbol], List[CallEdge]]:
        symbols: List[Symbol] = []
        edges: List[CallEdge] = []
        lines = content.splitlines()

        try:
            tree = ast.parse(content)
        except Exception:
            return self._extract_regex_symbols(repo_id, file_path, content, "python")

        class Visitor(ast.NodeVisitor):
            def __init__(self, outer):
                self.outer = outer
                self.current_class = None
                self.current_func = None

            def visit_ClassDef(self, node: ast.ClassDef):
                sym_id = f"{repo_id}:{file_path}:{node.name}"
                symbols.append(
                    Symbol(
                        symbol_id=sym_id,
                        repo_id=repo_id,
                        name=node.name,
                        kind="class",
                        file_path=file_path,
                        line_number=node.lineno,
                        signature=f"class {node.name}",
                        docstring=ast.get_docstring(node),
                    )
                )
                old_class = self.current_class
                self.current_class = node.name
                self.generic_visit(node)
                self.current_class = old_class

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._handle_func(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._handle_func(node)

            def _handle_func(self, node):
                kind = "method" if self.current_class else "function"
                sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}(...)"
                sym_id = f"{repo_id}:{file_path}:{self.current_class + '.' if self.current_class else ''}{node.name}"

                symbols.append(
                    Symbol(
                        symbol_id=sym_id,
                        repo_id=repo_id,
                        name=node.name,
                        kind=kind,
                        file_path=file_path,
                        line_number=node.lineno,
                        signature=sig,
                        docstring=ast.get_docstring(node),
                        parent_symbol=self.current_class,
                    )
                )

                old_func = self.current_func
                self.current_func = node.name
                self.generic_visit(node)
                self.current_func = old_func

            def visit_Call(self, node: ast.Call):
                target = ""
                if isinstance(node.func, ast.Name):
                    target = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    target = node.func.attr

                if target and self.current_func:
                    edge_id = f"{repo_id}:{file_path}:{self.current_func}->{target}:{node.lineno}"
                    edges.append(
                        CallEdge(
                            edge_id=edge_id,
                            source_repo=repo_id,
                            source_file=file_path,
                            source_symbol=self.current_func,
                            target_repo=None,
                            target_file=None,
                            target_symbol=target,
                            edge_type="CALLS",
                            line_number=node.lineno,
                        )
                    )
                self.generic_visit(node)

        Visitor(self).visit(tree)
        return symbols, edges

    def _extract_regex_symbols(
        self, repo_id: str, file_path: str, content: str, language: str
    ) -> Tuple[List[Symbol], List[CallEdge]]:
        symbols: List[Symbol] = []
        edges: List[CallEdge] = []
        lines = content.splitlines()

        patterns = [
            (
                re.compile(
                    r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)"
                ),
                "function",
            ),
            (re.compile(r"^(?:export\s+)?class\s+([A-Za-z0-9_$]+)"), "class"),
            (re.compile(r"^(?:export\s+)?interface\s+([A-Za-z0-9_$]+)"), "interface"),
            (re.compile(r"^(?:export\s+)?type\s+([A-Za-z0-9_$]+)\s*="), "type"),
            (re.compile(r"^(?:pub\s+)?fn\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)"), "function"),
            (
                re.compile(
                    r"^func\s+(?:\((?:[^)]+)\)\s+)?([A-Za-z0-9_]+)\s*\(([^)]*)\)"
                ),
                "function",
            ),
            (re.compile(r"^type\s+([A-Za-z0-9_]+)\s+struct"), "struct"),
            (
                re.compile(
                    r"^(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
                ),
                "function",
            ),
        ]

        for i, line in enumerate(lines):
            l_strip = line.strip()
            for pat, kind in patterns:
                m = pat.match(l_strip)
                if m:
                    sym_name = m.group(1)
                    sym_id = f"{repo_id}:{file_path}:{sym_name}"
                    symbols.append(
                        Symbol(
                            symbol_id=sym_id,
                            repo_id=repo_id,
                            name=sym_name,
                            kind=kind,
                            file_path=file_path,
                            line_number=i + 1,
                            signature=l_strip[:100],
                        )
                    )
                    break

            # Simple call tracking
            call_matches = re.findall(r"([A-Za-z0-9_$]+)\s*\(", l_strip)
            for c_target in call_matches:
                if c_target not in (
                    "if",
                    "for",
                    "while",
                    "switch",
                    "catch",
                    "return",
                    "sizeof",
                ):
                    edges.append(
                        CallEdge(
                            edge_id=f"{repo_id}:{file_path}:{i + 1}:{c_target}",
                            source_repo=repo_id,
                            source_file=file_path,
                            source_symbol=f"line_{i + 1}",
                            target_repo=None,
                            target_file=None,
                            target_symbol=c_target,
                            edge_type="CALLS",
                            line_number=i + 1,
                        )
                    )

        return symbols, edges

    def _extract_api_signatures(
        self, repo_id: str, file_path: str, content: str
    ) -> Tuple[List[Symbol], List[CallEdge]]:
        symbols: List[Symbol] = []
        edges: List[CallEdge] = []
        lines = content.splitlines()

        for idx, line in enumerate(lines):
            # Server endpoints
            for m in self.py_endpoint_pattern.finditer(line):
                method, path = m.group(1).upper(), m.group(2)
                endpoint_sig = f"{method} {path}"
                symbols.append(
                    Symbol(
                        symbol_id=f"{repo_id}:endpoint:{endpoint_sig}",
                        repo_id=repo_id,
                        name=endpoint_sig,
                        kind="api_endpoint",
                        file_path=file_path,
                        line_number=idx + 1,
                        signature=f"@{method.lower()}('{path}')",
                    )
                )

            for m in self.js_endpoint_pattern.finditer(line):
                method, path = m.group(1).upper(), m.group(2)
                endpoint_sig = f"{method} {path}"
                symbols.append(
                    Symbol(
                        symbol_id=f"{repo_id}:endpoint:{endpoint_sig}",
                        repo_id=repo_id,
                        name=endpoint_sig,
                        kind="api_endpoint",
                        file_path=file_path,
                        line_number=idx + 1,
                        signature=f"app.{method.lower()}('{path}')",
                    )
                )

            # Client calls
            for m in self.client_call_pattern.finditer(line):
                raw_path = m.group(1)
                clean_path = raw_path.split("?")[0]
                if "/" in clean_path:
                    edges.append(
                        CallEdge(
                            edge_id=f"{repo_id}:{file_path}:{idx + 1}:API_CALL:{clean_path}",
                            source_repo=repo_id,
                            source_file=file_path,
                            source_symbol=f"line_{idx + 1}",
                            target_repo=None,
                            target_file=None,
                            target_symbol=clean_path,
                            edge_type="CROSS_REPO_API",
                            line_number=idx + 1,
                        )
                    )

        return symbols, edges
