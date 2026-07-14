"""Persistent vector store on sqlite (metadata) + float32 blobs (vectors).

Brute-force cosine search. For Gainesville scale — a few hundred sites × ~20
chunks ≈ 10k vectors — this is instant and needs zero vector-DB dependency. The
interface (upsert_business / query / business_state) is deliberately small so a
LanceDB/pgvector backend can be swapped in later without touching the pipeline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id  TEXT NOT NULL,
    name         TEXT,
    category     TEXT,
    url          TEXT,
    chunk_idx    INTEGER,
    text         TEXT,
    vector       BLOB NOT NULL,
    fetched_at   TEXT,
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_biz ON chunks(business_id);

CREATE TABLE IF NOT EXISTS sites (
    business_id  TEXT PRIMARY KEY,
    name         TEXT,
    url          TEXT,
    fetched_at   TEXT,
    content_hash TEXT,
    n_chunks     INTEGER,
    status       TEXT          -- 'indexed' | 'prospect' | 'error'
);
"""


class VectorStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- write ----
    def upsert_business(self, biz_id, name, category, url, chunks, vectors,
                        fetched_at, content_hash):
        """Replace all chunks for a business (idempotent re-crawl)."""
        cur = self.db.cursor()
        cur.execute("DELETE FROM chunks WHERE business_id=?", (biz_id,))
        rows = [
            (biz_id, name, category, url, i, chunks[i],
             vectors[i].astype(np.float32).tobytes(), fetched_at, content_hash)
            for i in range(len(chunks))
        ]
        cur.executemany(
            "INSERT INTO chunks(business_id,name,category,url,chunk_idx,text,vector,fetched_at,content_hash)"
            " VALUES(?,?,?,?,?,?,?,?,?)", rows)
        cur.execute(
            "INSERT OR REPLACE INTO sites(business_id,name,url,fetched_at,content_hash,n_chunks,status)"
            " VALUES(?,?,?,?,?,?, 'indexed')",
            (biz_id, name, url, fetched_at, content_hash, len(chunks)))
        self.db.commit()

    def mark(self, biz_id, name, url, status):
        self.db.execute(
            "INSERT OR REPLACE INTO sites(business_id,name,url,fetched_at,content_hash,n_chunks,status)"
            " VALUES(?,?,?,COALESCE((SELECT fetched_at FROM sites WHERE business_id=?),NULL),NULL,"
            " COALESCE((SELECT n_chunks FROM sites WHERE business_id=?),0),?)",
            (biz_id, name, url, biz_id, biz_id, status))
        self.db.commit()

    # ---- read ----
    def business_state(self, biz_id) -> Optional[dict]:
        row = self.db.execute(
            "SELECT fetched_at, content_hash, status FROM sites WHERE business_id=?",
            (biz_id,)).fetchone()
        if not row:
            return None
        return {"fetched_at": row[0], "content_hash": row[1], "status": row[2]}

    def _load_matrix(self):
        rows = self.db.execute(
            "SELECT business_id,name,category,url,chunk_idx,text,vector FROM chunks").fetchall()
        if not rows:
            return None, []
        mat = np.stack([np.frombuffer(r[6], dtype=np.float32) for r in rows])
        meta = [dict(business_id=r[0], name=r[1], category=r[2], url=r[3],
                     chunk_idx=r[4], text=r[5]) for r in rows]
        return mat, meta

    def query(self, qvec: np.ndarray, k: int = 8):
        mat, meta = self._load_matrix()
        if mat is None:
            return []
        q = qvec.astype(np.float32).ravel()
        n = np.linalg.norm(q)
        if n:
            q = q / n
        scores = mat @ q  # vectors are stored L2-normalized → dot == cosine
        top = np.argsort(-scores)[:k]
        return [{**meta[i], "score": float(scores[i])} for i in top]

    def stats(self) -> dict:
        c = self.db.execute
        return {
            "sites_indexed": c("SELECT COUNT(*) FROM sites WHERE status='indexed'").fetchone()[0],
            "prospects": c("SELECT COUNT(*) FROM sites WHERE status='prospect'").fetchone()[0],
            "errors": c("SELECT COUNT(*) FROM sites WHERE status='error'").fetchone()[0],
            "chunks": c("SELECT COUNT(*) FROM chunks").fetchone()[0],
        }

    def prospects(self, limit: int = 1000):
        rows = self.db.execute(
            "SELECT name, url FROM sites WHERE status='prospect' LIMIT ?", (limit,)).fetchall()
        return [{"name": r[0], "url": r[1]} for r in rows]

    def close(self):
        self.db.close()
