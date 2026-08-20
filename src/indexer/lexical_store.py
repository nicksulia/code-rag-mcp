"""
BM25F (Field-Weighted BM25) lexical ranking engine for multi-repository codebases.
Distinguishes between symbol names, signatures, docstrings, and code body with specialized field weights.
"""

import math
import re
import json
import sqlite3
import contextlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set, Any
from ..models.schema import CodeChunk, ChunkType


FIELD_WEIGHTS = {"symbol": 4.0, "signature": 2.5, "docstring": 2.0, "body": 1.0}

FIELD_B_PARAMS = {"symbol": 0.5, "signature": 0.6, "docstring": 0.75, "body": 0.85}


class BM25LexicalStore:
    def __init__(self, data_dir: str = "./data", k1: float = 1.2):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "bm25f_v2.db"
        self.k1 = k1
        self._init_db()

    def _init_db(self):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunk_field_lengths (
                    chunk_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    len_symbol INTEGER NOT NULL,
                    len_signature INTEGER NOT NULL,
                    len_docstring INTEGER NOT NULL,
                    len_body INTEGER NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bm25f_index (
                    token TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    tf_symbol INTEGER DEFAULT 0,
                    tf_signature INTEGER DEFAULT 0,
                    tf_docstring INTEGER DEFAULT 0,
                    tf_body INTEGER DEFAULT 0,
                    PRIMARY KEY (token, chunk_id)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_bm25f_token ON bm25f_index(token)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_bm25f_repo ON bm25f_index(repo_id)"
            )
            conn.commit()

    def tokenize_code(self, text: Optional[str]) -> List[str]:
        if not text:
            return []
        words = re.findall(r"[A-Za-z0-9_]+", text.lower())
        tokens: List[str] = []
        for w in words:
            tokens.append(w)
            subparts = re.findall(r"[a-z]+|[0-9]+", w)
            if len(subparts) > 1:
                tokens.extend(subparts)
        return tokens

    def add_chunks(self, chunks: List[CodeChunk]):
        if not chunks:
            return

        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            for chunk in chunks:
                # 1. Field extraction
                sym_tokens = self.tokenize_code(chunk.symbol_name)
                sig_tokens = self.tokenize_code(
                    "\n".join(chunk.raw_content.splitlines()[:3])
                )
                doc_tokens = self.tokenize_code(chunk.docstring)
                body_tokens = self.tokenize_code(chunk.raw_content)

                # Lengths
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO chunk_field_lengths (
                        chunk_id, repo_id, len_symbol, len_signature, len_docstring, len_body
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        chunk.chunk_id,
                        chunk.repo_id,
                        len(sym_tokens),
                        len(sig_tokens),
                        len(doc_tokens),
                        len(body_tokens),
                    ),
                )

                # Token frequency aggregation across fields
                field_tfs: Dict[str, Dict[str, int]] = {}
                for t in set(sym_tokens + sig_tokens + doc_tokens + body_tokens):
                    field_tfs[t] = {
                        "symbol": sym_tokens.count(t),
                        "signature": sig_tokens.count(t),
                        "docstring": doc_tokens.count(t),
                        "body": body_tokens.count(t),
                    }

                for tok, counts in field_tfs.items():
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO bm25f_index (
                            token, chunk_id, repo_id, tf_symbol, tf_signature, tf_docstring, tf_body
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            tok,
                            chunk.chunk_id,
                            chunk.repo_id,
                            counts["symbol"],
                            counts["signature"],
                            counts["docstring"],
                            counts["body"],
                        ),
                    )

            conn.commit()

    def delete_repo_chunks(self, repo_id: str):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chunk_field_lengths WHERE repo_id = ?", (repo_id,)
            )
            cursor.execute("DELETE FROM bm25f_index WHERE repo_id = ?", (repo_id,))
            conn.commit()

    def delete_file_chunks(self, repo_id: str, chunk_ids: List[str]):
        if not chunk_ids:
            return
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in chunk_ids)
            cursor.execute(
                f"DELETE FROM chunk_field_lengths WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            cursor.execute(
                f"DELETE FROM bm25f_index WHERE chunk_id IN ({placeholders})", chunk_ids
            )
            conn.commit()

    def search_bm25(
        self, query: str, repo_ids: Optional[List[str]] = None, limit: int = 15
    ) -> List[Tuple[str, float, List[str]]]:
        """
        Executes BM25F ranking with field weights.
        Returns: List of (chunk_id, score, matched_tokens).
        """
        q_tokens = self.tokenize_code(query)
        if not q_tokens:
            return []

        unique_q_tokens = list(set(q_tokens))

        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()

            # Average field lengths across index
            if repo_ids:
                placeholders = ",".join("?" for _ in repo_ids)
                cursor.execute(
                    f"""
                    SELECT COUNT(*), AVG(len_symbol), AVG(len_signature), AVG(len_docstring), AVG(len_body)
                    FROM chunk_field_lengths WHERE repo_id IN ({placeholders})
                """,
                    repo_ids,
                )
            else:
                cursor.execute("""
                    SELECT COUNT(*), AVG(len_symbol), AVG(len_signature), AVG(len_docstring), AVG(len_body)
                    FROM chunk_field_lengths
                """)
            row = cursor.fetchone()
            N = row[0] or 1
            avg_lens = {
                "symbol": max(1.0, row[1] or 2.0),
                "signature": max(1.0, row[2] or 10.0),
                "docstring": max(1.0, row[3] or 15.0),
                "body": max(1.0, row[4] or 50.0),
            }

            chunk_scores: Dict[str, float] = {}
            matched_terms_map: Dict[str, List[str]] = {}

            # Cache doc field lengths
            doc_lengths: Dict[str, Dict[str, int]] = {}

            for q_tok in unique_q_tokens:
                if repo_ids:
                    placeholders = ",".join("?" for _ in repo_ids)
                    cursor.execute(
                        f"""
                        SELECT chunk_id, tf_symbol, tf_signature, tf_docstring, tf_body
                        FROM bm25f_index WHERE token = ? AND repo_id IN ({placeholders})
                    """,
                        [q_tok] + repo_ids,
                    )
                else:
                    cursor.execute(
                        """
                        SELECT chunk_id, tf_symbol, tf_signature, tf_docstring, tf_body
                        FROM bm25f_index WHERE token = ?
                    """,
                        (q_tok,),
                    )

                postings = cursor.fetchall()
                n_q = len(postings)
                if n_q == 0:
                    continue

                # Robertson-Spärck Jones IDF
                idf = math.log(1.0 + (N - n_q + 0.5) / (n_q + 0.5))
                if idf < 0:
                    idf = 0.05

                for chunk_id, tf_sym, tf_sig, tf_doc, tf_body in postings:
                    if chunk_id not in doc_lengths:
                        cursor.execute(
                            "SELECT len_symbol, len_signature, len_docstring, len_body FROM chunk_field_lengths WHERE chunk_id = ?",
                            (chunk_id,),
                        )
                        l_row = cursor.fetchone()
                        if l_row:
                            doc_lengths[chunk_id] = {
                                "symbol": l_row[0],
                                "signature": l_row[1],
                                "docstring": l_row[2],
                                "body": l_row[3],
                            }
                        else:
                            doc_lengths[chunk_id] = {
                                k: int(v) for k, v in avg_lens.items()
                            }

                    d_len = doc_lengths[chunk_id]

                    # BM25F normalized term frequency across all fields
                    tfe_sym = FIELD_WEIGHTS["symbol"] * (
                        tf_sym
                        / (
                            1.0
                            - FIELD_B_PARAMS["symbol"]
                            + FIELD_B_PARAMS["symbol"]
                            * (d_len["symbol"] / avg_lens["symbol"])
                        )
                    )
                    tfe_sig = FIELD_WEIGHTS["signature"] * (
                        tf_sig
                        / (
                            1.0
                            - FIELD_B_PARAMS["signature"]
                            + FIELD_B_PARAMS["signature"]
                            * (d_len["signature"] / avg_lens["signature"])
                        )
                    )
                    tfe_doc = FIELD_WEIGHTS["docstring"] * (
                        tf_doc
                        / (
                            1.0
                            - FIELD_B_PARAMS["docstring"]
                            + FIELD_B_PARAMS["docstring"]
                            * (d_len["docstring"] / avg_lens["docstring"])
                        )
                    )
                    tfe_body = FIELD_WEIGHTS["body"] * (
                        tf_body
                        / (
                            1.0
                            - FIELD_B_PARAMS["body"]
                            + FIELD_B_PARAMS["body"]
                            * (d_len["body"] / avg_lens["body"])
                        )
                    )

                    total_tfe = tfe_sym + tfe_sig + tfe_doc + tfe_body

                    # Term score
                    term_score = idf * (total_tfe / (self.k1 + total_tfe))
                    chunk_scores[chunk_id] = (
                        chunk_scores.get(chunk_id, 0.0) + term_score
                    )
                    if chunk_id not in matched_terms_map:
                        matched_terms_map[chunk_id] = []
                    matched_terms_map[chunk_id].append(q_tok)

        sorted_results = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
        return [
            (cid, score, matched_terms_map.get(cid, []))
            for cid, score in sorted_results[:limit]
        ]
