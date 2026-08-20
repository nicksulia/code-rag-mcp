"""
Multi-Vector dense storage and similarity search engine.
Indexes dual vectors per code chunk: Signature/Intent Vector (V_sig) and Implementation Vector (V_body).
"""

import math
import json
import sqlite3
import contextlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any, Callable
from ..models.schema import CodeChunk, ChunkType
from .embeddings import BaseEmbeddingEngine, EmbeddingFactory


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, dot))


class VectorStore:
    def __init__(
        self,
        data_dir: str = "./data",
        embedding_engine: Optional[BaseEmbeddingEngine] = None,
        dimension_provider: Optional[Callable[[], Optional[int]]] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "vectors_v2.db"
        self.embedding_engine = embedding_engine or EmbeddingFactory.create(dim=384)
        # Lazy: never read engine.dimension at construction time, that would
        # pull the embedding model into memory at startup.
        self._dimension_provider = dimension_provider
        self._init_db()

    @property
    def dimension(self) -> Optional[int]:
        """Resolved lazily from the provider, else from the engine on demand."""
        if self._dimension_provider is not None:
            try:
                dim = self._dimension_provider()
            except Exception:
                dim = None
            if dim:
                return int(dim)
        return self.embedding_engine.dimension

    def _init_db(self):
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks_v2 (
                    chunk_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    raw_content TEXT NOT NULL,
                    enriched_content TEXT NOT NULL,
                    symbol_name TEXT,
                    chunk_type TEXT NOT NULL,
                    parent_symbol TEXT,
                    imports_json TEXT,
                    docstring TEXT,
                    sig_embedding_json TEXT NOT NULL,
                    body_embedding_json TEXT NOT NULL
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_v2_repo ON chunks_v2(repo_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_v2_file ON chunks_v2(repo_id, file_path)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_v2_symbol ON chunks_v2(symbol_name)"
            )
            conn.commit()

    def _extract_signature_text(self, chunk: CodeChunk) -> str:
        parts = []
        if chunk.symbol_name:
            parts.append(f"symbol: {chunk.symbol_name}")
        if chunk.parent_symbol:
            parts.append(f"scope: {chunk.parent_symbol}")
        parts.append(
            f"kind: {chunk.chunk_type.value if hasattr(chunk.chunk_type, 'value') else chunk.chunk_type}"
        )
        if chunk.docstring:
            parts.append(f"doc: {chunk.docstring}")
        if chunk.imports:
            parts.append(f"imports: {', '.join(chunk.imports[:3])}")

        # Add first 3 lines of raw content (usually declaration/signature)
        first_lines = "\n".join(chunk.raw_content.splitlines()[:3])
        parts.append(first_lines)
        return "\n".join(parts)

    def add_chunks(self, chunks: List[CodeChunk]):
        if not chunks:
            return

        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            for chunk in chunks:
                sig_text = self._extract_signature_text(chunk)
                body_text = chunk.raw_content

                sig_vec = self.embedding_engine.encode_text(
                    sig_text, field_type="signature"
                )
                body_vec = self.embedding_engine.encode_text(
                    body_text, field_type="body"
                )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO chunks_v2 (
                        chunk_id, repo_id, file_path, language,
                        start_line, end_line, raw_content, enriched_content,
                        symbol_name, chunk_type, parent_symbol,
                        imports_json, docstring, sig_embedding_json, body_embedding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        chunk.chunk_id,
                        chunk.repo_id,
                        chunk.file_path,
                        chunk.language,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.raw_content,
                        chunk.enriched_content,
                        chunk.symbol_name,
                        chunk.chunk_type.value
                        if isinstance(chunk.chunk_type, ChunkType)
                        else chunk.chunk_type,
                        chunk.parent_symbol,
                        json.dumps(chunk.imports),
                        chunk.docstring,
                        json.dumps(sig_vec),
                        json.dumps(body_vec),
                    ),
                )
            conn.commit()

    def delete_repo_chunks(self, repo_id: str):
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chunks_v2 WHERE repo_id = ?", (repo_id,))
            conn.commit()

    def delete_file_chunks(self, repo_id: str, file_path: str) -> List[str]:
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT chunk_id FROM chunks_v2 WHERE repo_id = ? AND file_path = ?",
                (repo_id, file_path),
            )
            chunk_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                "DELETE FROM chunks_v2 WHERE repo_id = ? AND file_path = ?",
                (repo_id, file_path),
            )
            conn.commit()
            return chunk_ids

    def search_vector(
        self,
        query: str,
        repo_ids: Optional[List[str]] = None,
        limit: int = 15,
        sig_weight: float = 0.6,
    ) -> List[Tuple[CodeChunk, float]]:
        """
        Dual-vector semantic search combining Signature and Body embeddings.
        """
        query_sig_vec = self.embedding_engine.encode_text(query, field_type="signature")
        query_body_vec = self.embedding_engine.encode_text(query, field_type="body")

        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            if repo_ids:
                placeholders = ",".join("?" for _ in repo_ids)
                cursor.execute(
                    f"SELECT * FROM chunks_v2 WHERE repo_id IN ({placeholders})",
                    repo_ids,
                )
            else:
                cursor.execute("SELECT * FROM chunks_v2")

            rows = cursor.fetchall()
            scored: List[Tuple[CodeChunk, float]] = []

            for row in rows:
                chunk = CodeChunk(
                    chunk_id=row[0],
                    repo_id=row[1],
                    file_path=row[2],
                    language=row[3],
                    start_line=row[4],
                    end_line=row[5],
                    raw_content=row[6],
                    enriched_content=row[7],
                    symbol_name=row[8],
                    chunk_type=ChunkType(row[9]),
                    parent_symbol=row[10],
                    imports=json.loads(row[11]) if row[11] else [],
                    docstring=row[12],
                )
                sig_emb = json.loads(row[13])
                body_emb = json.loads(row[14])

                sim_sig = cosine_similarity(query_sig_vec, sig_emb)
                sim_body = cosine_similarity(query_body_vec, body_emb)

                # Composite weighted similarity
                composite_sim = (sig_weight * sim_sig) + ((1.0 - sig_weight) * sim_body)

                if composite_sim > 0.05:
                    scored.append((chunk, composite_sim))

            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:limit]

    def get_chunk(self, chunk_id: str) -> Optional[CodeChunk]:
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chunks_v2 WHERE chunk_id = ?", (chunk_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return CodeChunk(
                chunk_id=row[0],
                repo_id=row[1],
                file_path=row[2],
                language=row[3],
                start_line=row[4],
                end_line=row[5],
                raw_content=row[6],
                enriched_content=row[7],
                symbol_name=row[8],
                chunk_type=ChunkType(row[9]),
                parent_symbol=row[10],
                imports=json.loads(row[11]) if row[11] else [],
                docstring=row[12],
            )

    def count_chunks(self, repo_id: Optional[str] = None) -> int:
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM chunks_v2 WHERE repo_id = ?", (repo_id,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM chunks_v2")
            return cursor.fetchone()[0]

    def list_indexed_repo_ids(self) -> List[str]:
        """Repositories that currently have dense vectors stored."""
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT repo_id FROM chunks_v2")
            return [row[0] for row in cursor.fetchall()]

    def reembed_repo(
        self,
        repo_id: str,
        progress_callback: Optional[Any] = None,
        batch_size: int = 64,
    ) -> Dict[str, Any]:
        """
        Rebuilds dense vectors for a repository from the chunks already stored.

        Chunk text, symbol graph, and lexical index are model-independent and are
        left untouched: this is an embedding-only pass, not a re-parse.
        """
        with contextlib.closing(
            sqlite3.connect(str(self.db_path), timeout=30.0)
        ) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT chunk_id FROM chunks_v2 WHERE repo_id = ?", (repo_id,)
            )
            chunk_ids = [row[0] for row in cursor.fetchall()]

        total = len(chunk_ids)
        processed = 0
        dimension: Optional[int] = None

        for start in range(0, total, batch_size):
            batch_ids = chunk_ids[start : start + batch_size]
            placeholders = ",".join("?" for _ in batch_ids)
            with contextlib.closing(
                sqlite3.connect(str(self.db_path), timeout=30.0)
            ) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT * FROM chunks_v2 WHERE chunk_id IN ({placeholders})",
                    batch_ids,
                )
                rows = cursor.fetchall()

                updates = []
                for row in rows:
                    chunk = CodeChunk(
                        chunk_id=row[0],
                        repo_id=row[1],
                        file_path=row[2],
                        language=row[3],
                        start_line=row[4],
                        end_line=row[5],
                        raw_content=row[6],
                        enriched_content=row[7],
                        symbol_name=row[8],
                        chunk_type=ChunkType(row[9]),
                        parent_symbol=row[10],
                        imports=json.loads(row[11]) if row[11] else [],
                        docstring=row[12],
                    )
                    sig_vec = self.embedding_engine.encode_text(
                        self._extract_signature_text(chunk), field_type="signature"
                    )
                    body_vec = self.embedding_engine.encode_text(
                        chunk.raw_content, field_type="body"
                    )
                    if sig_vec:
                        dimension = len(sig_vec)
                    updates.append(
                        (json.dumps(sig_vec), json.dumps(body_vec), chunk.chunk_id)
                    )

                cursor.executemany(
                    "UPDATE chunks_v2 SET sig_embedding_json = ?, body_embedding_json = ? WHERE chunk_id = ?",
                    updates,
                )
                conn.commit()

            processed += len(rows)
            if progress_callback:
                progress_callback(
                    processed,
                    total,
                    f"Re-embedding '{repo_id}' ({processed}/{total} chunks)",
                    "reembed",
                )

        return {"repo_id": repo_id, "chunks": processed, "dimension": dimension}
