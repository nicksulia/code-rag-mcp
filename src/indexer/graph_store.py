"""
Graph database for symbol definitions, call hierarchies, cross-repo dependencies, and centrality.
"""

import math
import sqlite3
import contextlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from ..models.schema import Symbol, CallEdge


class GraphStore:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "graph.db"
        self._init_db()

    def _init_db(self):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    signature TEXT,
                    docstring TEXT,
                    parent_symbol TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS call_edges (
                    edge_id TEXT PRIMARY KEY,
                    source_repo TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    target_repo TEXT,
                    target_file TEXT,
                    target_symbol TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    line_number INTEGER NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sym_repo ON symbols(repo_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(repo_id, file_path)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_target ON call_edges(target_symbol)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_source ON call_edges(source_repo, source_symbol)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_type ON call_edges(edge_type)"
            )
            conn.commit()

    def add_symbols_and_edges(self, symbols: List[Symbol], edges: List[CallEdge]):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            for s in symbols:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO symbols (
                        symbol_id, repo_id, name, kind, file_path, line_number, signature, docstring, parent_symbol
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        s.symbol_id,
                        s.repo_id,
                        s.name,
                        s.kind,
                        s.file_path,
                        s.line_number,
                        s.signature,
                        s.docstring,
                        s.parent_symbol,
                    ),
                )

            for e in edges:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO call_edges (
                        edge_id, source_repo, source_file, source_symbol, target_repo, target_file, target_symbol, edge_type, line_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        e.edge_id,
                        e.source_repo,
                        e.source_file,
                        e.source_symbol,
                        e.target_repo,
                        e.target_file,
                        e.target_symbol,
                        e.edge_type,
                        e.line_number,
                    ),
                )
            conn.commit()

    def delete_repo_data(self, repo_id: str):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM symbols WHERE repo_id = ?", (repo_id,))
            cursor.execute("DELETE FROM call_edges WHERE source_repo = ?", (repo_id,))
            conn.commit()

    def delete_file_data(self, repo_id: str, file_path: str):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM symbols WHERE repo_id = ? AND file_path = ?",
                (repo_id, file_path),
            )
            cursor.execute(
                "DELETE FROM call_edges WHERE source_repo = ? AND source_file = ?",
                (repo_id, file_path),
            )
            conn.commit()

    def get_symbol_definition(
        self, name: str, repo_id: Optional[str] = None
    ) -> List[Symbol]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute(
                    "SELECT * FROM symbols WHERE name = ? AND repo_id = ?",
                    (name, repo_id),
                )
            else:
                cursor.execute("SELECT * FROM symbols WHERE name = ?", (name,))

            rows = cursor.fetchall()
            return [
                Symbol(
                    symbol_id=r[0],
                    repo_id=r[1],
                    name=r[2],
                    kind=r[3],
                    file_path=r[4],
                    line_number=r[5],
                    signature=r[6],
                    docstring=r[7],
                    parent_symbol=r[8],
                )
                for r in rows
            ]

    def get_callers(
        self, target_symbol: str, repo_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Finds who calls `target_symbol` across repos."""
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute(
                    """
                    SELECT source_repo, source_file, source_symbol, edge_type, line_number
                    FROM call_edges WHERE target_symbol = ? AND source_repo = ?
                """,
                    (target_symbol, repo_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT source_repo, source_file, source_symbol, edge_type, line_number
                    FROM call_edges WHERE target_symbol = ?
                """,
                    (target_symbol,),
                )

            rows = cursor.fetchall()
            return [
                {
                    "source_repo": r[0],
                    "source_file": r[1],
                    "source_symbol": r[2],
                    "edge_type": r[3],
                    "line_number": r[4],
                }
                for r in rows
            ]

    def get_callees(self, source_symbol: str, repo_id: str) -> List[Dict[str, Any]]:
        """Finds who is called by `source_symbol` in `repo_id`."""
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT target_symbol, target_repo, target_file, edge_type, line_number
                FROM call_edges WHERE source_repo = ? AND source_symbol = ?
            """,
                (repo_id, source_symbol),
            )
            rows = cursor.fetchall()
            return [
                {
                    "target_symbol": r[0],
                    "target_repo": r[1],
                    "target_file": r[2],
                    "edge_type": r[3],
                    "line_number": r[4],
                }
                for r in rows
            ]

    def get_symbol_centrality(
        self, symbol_name: str, owning_repo: Optional[str] = None
    ) -> float:
        """
        Computes the architectural centrality of a symbol based on in-degree and cross-repo referencing.
        """
        if not symbol_name:
            return 0.0

        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT source_repo, COUNT(*) 
                FROM call_edges 
                WHERE target_symbol = ? OR target_symbol LIKE '%' || ? || '%'
                GROUP BY source_repo
            """,
                (symbol_name, symbol_name),
            )

            rows = cursor.fetchall()
            if not rows:
                return 0.0

            total_calls = sum(r[1] for r in rows)
            distinct_repos = len(rows)

            # Bonus for cross-repo usage (bridge centrality)
            cross_repo_multiplier = (
                1.8
                if (owning_repo and any(r[0] != owning_repo for r in rows))
                or distinct_repos > 1
                else 1.0
            )
            centrality = (math.log(1.0 + total_calls) * 0.02) * cross_repo_multiplier
            return min(0.08, centrality)

    def get_cross_repo_api_links(self) -> List[Dict[str, Any]]:
        """Finds cross-repo linkages between client calls and server endpoints."""
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.source_repo, e.source_file, e.source_symbol, e.target_symbol, e.line_number,
                       s.repo_id as server_repo, s.file_path as server_file, s.line_number as server_line
                FROM call_edges e
                JOIN symbols s ON s.kind = 'api_endpoint' AND (s.name LIKE '%' || e.target_symbol || '%' OR e.target_symbol LIKE '%' || s.name || '%')
                WHERE e.edge_type = 'CROSS_REPO_API' AND e.source_repo != s.repo_id
            """)
            rows = cursor.fetchall()
            return [
                {
                    "client_repo": r[0],
                    "client_file": r[1],
                    "client_symbol": r[2],
                    "api_route": r[3],
                    "client_line": r[4],
                    "server_repo": r[5],
                    "server_file": r[6],
                    "server_line": r[7],
                }
                for r in rows
            ]

    def count_symbols(self, repo_id: Optional[str] = None) -> int:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM symbols WHERE repo_id = ?", (repo_id,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM symbols")
            return cursor.fetchone()[0]
