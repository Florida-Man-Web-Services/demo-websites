"""Relational call store: outcomes on `calls`, full text in separate tables.

Schema (SQLite):

  calls
    id, call_sid UNIQUE, timestamp, direction, business, slug, phone,
    outcome, email, callback_time, notes, source, ended_at,
    transcript_id  → transcripts.id   (NULL until turns are flushed)

  transcripts
    id, call_id UNIQUE → calls.id, created_at, backend

  transcript_turns
    id, transcript_id → transcripts.id, seq, role, content, ts

`transcript_ref` returned to callers is a stable pointer string:
  calldb:transcript:<transcript_id>
which changerequests / operators can store without embedding the full text.

CSV dual-write stays for backward compatibility (call.py queue, ops grepping).
"""

from __future__ import annotations

import csv
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import config

log = logging.getLogger("voice-agent.calldb")

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid      TEXT    NOT NULL UNIQUE,
    timestamp     TEXT    NOT NULL,
    direction     TEXT    NOT NULL DEFAULT '',
    business      TEXT    NOT NULL DEFAULT '',
    slug          TEXT    NOT NULL DEFAULT '',
    phone         TEXT    NOT NULL DEFAULT '',
    outcome       TEXT    NOT NULL DEFAULT '',
    email         TEXT    NOT NULL DEFAULT '',
    callback_time TEXT    NOT NULL DEFAULT '',
    notes         TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT '',
    ended_at      TEXT    NOT NULL DEFAULT '',
    transcript_id INTEGER
        REFERENCES transcripts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    INTEGER NOT NULL UNIQUE
        REFERENCES calls(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    backend    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcript_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL
        REFERENCES transcripts(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    role          TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    ts            TEXT    NOT NULL DEFAULT '',
    UNIQUE (transcript_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_calls_slug ON calls(slug);
CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls(outcome);
CREATE INDEX IF NOT EXISTS idx_turns_transcript ON transcript_turns(transcript_id);
"""

# SQLite needs the referenced table to exist before the FK on calls.transcript_id
# can be applied. Create transcripts first without the reverse uniqueness dance
# by creating tables in two passes: core tables without the forward FK, then
# recreate is awkward — so create transcripts + turns first, then calls with FK.
_SCHEMA_ORDERED = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS transcripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    INTEGER NOT NULL UNIQUE,
    created_at TEXT    NOT NULL,
    backend    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcript_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL
        REFERENCES transcripts(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    role          TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    ts            TEXT    NOT NULL DEFAULT '',
    UNIQUE (transcript_id, seq)
);

CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid      TEXT    NOT NULL UNIQUE,
    timestamp     TEXT    NOT NULL,
    direction     TEXT    NOT NULL DEFAULT '',
    business      TEXT    NOT NULL DEFAULT '',
    slug          TEXT    NOT NULL DEFAULT '',
    phone         TEXT    NOT NULL DEFAULT '',
    outcome       TEXT    NOT NULL DEFAULT '',
    email         TEXT    NOT NULL DEFAULT '',
    callback_time TEXT    NOT NULL DEFAULT '',
    notes         TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT '',
    ended_at      TEXT    NOT NULL DEFAULT '',
    transcript_id INTEGER
        REFERENCES transcripts(id) ON DELETE SET NULL
);

-- Deferred FK transcripts.call_id → calls.id enforced in application code
-- (SQLite cannot easily circular-FK at CREATE time). Integrity is maintained
-- by upsert_call / save_transcript always creating the call row first.

CREATE INDEX IF NOT EXISTS idx_calls_slug ON calls(slug);
CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls(outcome);
CREATE INDEX IF NOT EXISTS idx_turns_transcript ON transcript_turns(transcript_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_call ON transcripts(call_id);
"""

_lock = threading.RLock()
_initialized_paths: set[str] = set()


def _db_path() -> Path:
    p = getattr(config, "CALL_DB", None)
    if p is None:
        raise RuntimeError("CALL_DB is disabled")
    return Path(p)


def enabled() -> bool:
    """False when CALL_DB is None / unset (tests can disable)."""
    raw = getattr(config, "CALL_DB", None)
    return raw is not None and str(raw).strip() != ""


def transcript_ref_for(transcript_id: int | None) -> str:
    if not transcript_id:
        return ""
    return f"calldb:transcript:{int(transcript_id)}"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
    finally:
        conn.close()


def init_db(path: Path | None = None) -> Path:
    """Create tables if needed. Safe to call repeatedly."""
    if path is not None:
        # Test hook: temporarily point config at another file.
        config.CALL_DB = path  # type: ignore[misc]
    if not enabled():
        return Path()
    dbp = _db_path()
    key = str(dbp.resolve()) if dbp else ""
    with _lock:
        if key in _initialized_paths and dbp.exists():
            return dbp
        with _conn() as conn:
            conn.executescript(_SCHEMA_ORDERED)
        _initialized_paths.add(key)
        _maybe_migrate_csv(dbp)
    return dbp


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _maybe_migrate_csv(dbp: Path) -> None:
    """One-shot import of legacy call-log.csv rows that are missing from SQLite."""
    csv_path = Path(getattr(config, "CALL_LOG", "") or "")
    if not csv_path or not csv_path.exists():
        return
    try:
        with _conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM calls").fetchone()["c"]
            if n > 0:
                return  # already populated; don't re-import forever
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return
            for row in rows:
                sid = (row.get("call_sid") or "").strip()
                if not sid:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO calls (
                        call_sid, timestamp, direction, business, slug, phone,
                        outcome, email, callback_time, notes, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        row.get("timestamp") or _now(),
                        row.get("direction") or "",
                        row.get("business") or "",
                        row.get("slug") or "",
                        row.get("phone") or "",
                        row.get("outcome") or "",
                        row.get("email") or "",
                        row.get("callback_time") or "",
                        row.get("notes") or "",
                        "csv-migrate",
                    ),
                )
            log.info("migrated %d call-log.csv rows into %s", len(rows), dbp)
    except Exception:
        log.exception("CSV → calldb migration skipped")


def upsert_call(
    *,
    call_sid: str,
    direction: str = "",
    business: str = "",
    slug: str = "",
    phone: str = "",
    outcome: str = "",
    email: str = "",
    callback_time: str = "",
    notes: str = "",
    source: str = "voice-agent",
    timestamp: str | None = None,
    ended_at: str | None = None,
) -> int:
    """Insert or update a calls row. Returns calls.id."""
    if not enabled() or not call_sid:
        return 0
    init_db()
    ts = timestamp or _now()
    with _lock, _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM calls WHERE call_sid = ?", (call_sid,)
        ).fetchone()
        if existing is None:
            cur = conn.execute(
                """
                INSERT INTO calls (
                    call_sid, timestamp, direction, business, slug, phone,
                    outcome, email, callback_time, notes, source, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_sid,
                    ts,
                    direction or "",
                    business or "",
                    slug or "",
                    phone or "",
                    outcome or "",
                    email or "",
                    callback_time or "",
                    notes or "",
                    source or "",
                    ended_at or "",
                ),
            )
            rid = cur.lastrowid
            return int(rid) if rid is not None else 0
        # Update non-empty fields only so a late flush doesn't wipe outcome.
        row = conn.execute(
            "SELECT * FROM calls WHERE id = ?", (existing["id"],)
        ).fetchone()
        conn.execute(
            """
            UPDATE calls SET
                direction     = CASE WHEN ? != '' THEN ? ELSE direction END,
                business      = CASE WHEN ? != '' THEN ? ELSE business END,
                slug          = CASE WHEN ? != '' THEN ? ELSE slug END,
                phone         = CASE WHEN ? != '' THEN ? ELSE phone END,
                outcome       = CASE WHEN ? != '' THEN ? ELSE outcome END,
                email         = CASE WHEN ? != '' THEN ? ELSE email END,
                callback_time = CASE WHEN ? != '' THEN ? ELSE callback_time END,
                notes         = CASE WHEN ? != '' THEN ? ELSE notes END,
                source        = CASE WHEN ? != '' THEN ? ELSE source END,
                ended_at      = CASE WHEN ? != '' THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (
                direction, direction,
                business, business,
                slug, slug,
                phone, phone,
                outcome, outcome,
                email, email,
                callback_time, callback_time,
                notes, notes,
                source, source,
                ended_at or "", ended_at or "",
                existing["id"],
            ),
        )
        return int(row["id"])


def save_transcript(
    call_sid: str,
    turns: Iterable[dict[str, Any]],
    *,
    backend: str = "",
    replace: bool = True,
) -> dict[str, Any]:
    """Persist turns for a call in transcripts + transcript_turns.

    Each turn: {role, content|text, ts?}
    Empty turns → no transcript row (transcript_id stays NULL).
    Returns {call_id, transcript_id, transcript_ref, turn_count}.
    """
    empty = {
        "call_id": 0,
        "transcript_id": None,
        "transcript_ref": "",
        "turn_count": 0,
    }
    if not enabled() or not call_sid:
        return empty
    init_db()
    cleaned: list[tuple[str, str, str]] = []
    for i, t in enumerate(turns):
        role = str(t.get("role") or "").strip() or "unknown"
        content = str(t.get("content") if t.get("content") is not None else t.get("text") or "").strip()
        if not content:
            continue
        ts = str(t.get("ts") or t.get("timestamp") or "")
        cleaned.append((role, content, ts))
    if not cleaned:
        return empty

    with _lock, _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            call = conn.execute(
                "SELECT id, transcript_id FROM calls WHERE call_sid = ?",
                (call_sid,),
            ).fetchone()
            if call is None:
                # Outcome may not have been logged yet (hangup before tool).
                cur = conn.execute(
                    """
                    INSERT INTO calls (call_sid, timestamp, source)
                    VALUES (?, ?, ?)
                    """,
                    (call_sid, _now(), "voice-agent"),
                )
                rid = cur.lastrowid
                call_id = int(rid) if rid is not None else 0
                old_tid = None
            else:
                call_id = int(call["id"])
                old_tid = call["transcript_id"]

            if old_tid and replace:
                conn.execute(
                    "UPDATE calls SET transcript_id = NULL WHERE id = ?",
                    (call_id,),
                )
                conn.execute("DELETE FROM transcripts WHERE id = ?", (old_tid,))
            elif old_tid and not replace:
                otid = int(old_tid)
                conn.execute("COMMIT")
                return {
                    "call_id": call_id,
                    "transcript_id": otid,
                    "transcript_ref": transcript_ref_for(otid),
                    "turn_count": conn.execute(
                        "SELECT COUNT(*) AS c FROM transcript_turns WHERE transcript_id = ?",
                        (otid,),
                    ).fetchone()["c"],
                }

            cur = conn.execute(
                """
                INSERT INTO transcripts (call_id, created_at, backend)
                VALUES (?, ?, ?)
                """,
                (call_id, _now(), backend or ""),
            )
            rid = cur.lastrowid
            tid = int(rid) if rid is not None else 0
            conn.executemany(
                """
                INSERT INTO transcript_turns (transcript_id, seq, role, content, ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(tid, seq, role, content, ts) for seq, (role, content, ts) in enumerate(cleaned)],
            )
            conn.execute(
                "UPDATE calls SET transcript_id = ?, ended_at = CASE WHEN ended_at = '' THEN ? ELSE ended_at END WHERE id = ?",
                (tid, _now(), call_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {
        "call_id": call_id,
        "transcript_id": tid,
        "transcript_ref": transcript_ref_for(tid),
        "turn_count": len(cleaned),
    }


def get_call(call_sid: str) -> dict[str, Any] | None:
    if not enabled() or not call_sid:
        return None
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM calls WHERE call_sid = ?", (call_sid,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["transcript_ref"] = transcript_ref_for(d.get("transcript_id"))
        return d


def get_transcript(call_sid: str | None = None, transcript_id: int | None = None) -> dict[str, Any] | None:
    """Load a transcript + ordered turns by call_sid or transcript id."""
    if not enabled():
        return None
    init_db()
    with _conn() as conn:
        if transcript_id is not None:
            trow = conn.execute(
                "SELECT t.*, c.call_sid FROM transcripts t JOIN calls c ON c.id = t.call_id WHERE t.id = ?",
                (int(transcript_id),),
            ).fetchone()
        elif call_sid:
            trow = conn.execute(
                """
                SELECT t.*, c.call_sid FROM transcripts t
                JOIN calls c ON c.id = t.call_id
                WHERE c.call_sid = ?
                """,
                (call_sid,),
            ).fetchone()
        else:
            return None
        if trow is None:
            return None
        turns = [
            dict(r)
            for r in conn.execute(
                """
                SELECT seq, role, content, ts
                FROM transcript_turns
                WHERE transcript_id = ?
                ORDER BY seq ASC
                """,
                (trow["id"],),
            ).fetchall()
        ]
        return {
            "transcript_id": trow["id"],
            "call_id": trow["call_id"],
            "call_sid": trow["call_sid"],
            "created_at": trow["created_at"],
            "backend": trow["backend"],
            "transcript_ref": transcript_ref_for(trow["id"]),
            "turns": turns,
        }


def history_for(slug: str) -> list[dict[str, Any]]:
    """Call rows for a business slug (outcome summary; no turn bodies)."""
    if not enabled() or not slug:
        return []
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT call_sid, timestamp, direction, business, slug, phone,
                   outcome, email, callback_time, notes, transcript_id
            FROM calls
            WHERE slug = ?
            ORDER BY timestamp ASC
            """,
            (slug,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["transcript_ref"] = transcript_ref_for(d.pop("transcript_id", None))
            out.append(d)
        return out


def called_slugs(*, include_test: bool = False) -> set[str]:
    if not enabled():
        return set()
    init_db()
    with _conn() as conn:
        rows = conn.execute("SELECT slug, call_sid FROM calls WHERE slug != ''").fetchall()
    out: set[str] = set()
    for r in rows:
        sid = r["call_sid"] or ""
        if not include_test and sid.startswith("TEST-"):
            continue
        out.add(r["slug"])
    return out


def extract_pipeline_turns(messages: list) -> list[dict[str, str]]:
    """Normalize Claude/Grok message history into caller/agent/tool turns."""
    turns: list[dict[str, str]] = []
    skip_prefixes = (
        "<call connected",
        "<silence",
    )
    for msg in messages or []:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role == "user":
            if isinstance(content, str):
                text = content.strip()
                if text and not text.lower().startswith(skip_prefixes):
                    turns.append({"role": "caller", "content": text, "ts": _now()})
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        body = block.get("content") or ""
                        if isinstance(body, list):
                            body = " ".join(
                                str(b.get("text", b)) if isinstance(b, dict) else str(b)
                                for b in body
                            )
                        turns.append(
                            {
                                "role": "tool",
                                "content": f"[tool_result {block.get('tool_use_id', '')}] {body}",
                                "ts": _now(),
                            }
                        )
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            turns.append({"role": "caller", "content": text, "ts": _now()})
        elif role == "assistant":
            if isinstance(content, str):
                text = content.strip()
                if text:
                    turns.append({"role": "agent", "content": text, "ts": _now()})
            elif isinstance(content, list):
                texts: list[str] = []
                for block in content:
                    btype = getattr(block, "type", None) or (
                        block.get("type") if isinstance(block, dict) else None
                    )
                    if btype == "text":
                        t = getattr(block, "text", None) or (
                            block.get("text") if isinstance(block, dict) else ""
                        )
                        if t and str(t).strip():
                            texts.append(str(t).strip())
                    elif btype == "tool_use":
                        name = getattr(block, "name", None) or (
                            block.get("name") if isinstance(block, dict) else ""
                        )
                        args = getattr(block, "input", None) or (
                            block.get("input") if isinstance(block, dict) else {}
                        )
                        turns.append(
                            {
                                "role": "tool",
                                "content": f"[tool_use {name}] {args}",
                                "ts": _now(),
                            }
                        )
                if texts:
                    turns.append({"role": "agent", "content": " ".join(texts), "ts": _now()})
    return turns


def append_csv_outcome(
    *,
    call_sid: str,
    direction: str,
    business: str,
    slug: str,
    phone: str,
    outcome: str,
    email: str = "",
    callback_time: str = "",
    notes: str = "",
    timestamp: str | None = None,
) -> None:
    """Legacy CSV dual-write (same columns as before)."""
    path = Path(config.CALL_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    ts = timestamp or _now()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(
                [
                    "timestamp",
                    "call_sid",
                    "direction",
                    "business",
                    "slug",
                    "phone",
                    "outcome",
                    "email",
                    "callback_time",
                    "notes",
                ]
            )
        writer.writerow(
            [
                ts,
                call_sid,
                direction,
                business,
                slug,
                phone,
                outcome,
                email,
                callback_time,
                notes,
            ]
        )


def log_outcome(
    *,
    call_sid: str,
    direction: str,
    business: str,
    slug: str,
    phone: str,
    outcome: str,
    email: str = "",
    callback_time: str = "",
    notes: str = "",
    source: str = "voice-agent",
    dual_write_csv: bool = True,
) -> dict[str, Any]:
    """Write outcome to SQLite (and optionally CSV)."""
    ts = _now()
    call_id = 0
    if enabled():
        call_id = upsert_call(
            call_sid=call_sid,
            direction=direction,
            business=business,
            slug=slug,
            phone=phone,
            outcome=outcome,
            email=email,
            callback_time=callback_time,
            notes=notes,
            source=source,
            timestamp=ts,
        )
    if dual_write_csv:
        append_csv_outcome(
            call_sid=call_sid,
            direction=direction,
            business=business,
            slug=slug,
            phone=phone,
            outcome=outcome,
            email=email,
            callback_time=callback_time,
            notes=notes,
            timestamp=ts,
        )
    ref = ""
    if enabled() and call_sid:
        row = get_call(call_sid)
        if row:
            ref = row.get("transcript_ref") or ""
    return {
        "logged": True,
        "call_id": call_id,
        "call_sid": call_sid,
        "transcript_ref": ref,
        "timestamp": ts,
    }


def finalize_call(
    call_sid: str,
    turns: Iterable[dict[str, Any]] | None = None,
    *,
    backend: str = "",
    direction: str = "",
    business: str = "",
    slug: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Ensure call row exists, mark ended, save transcript turns if any."""
    if not enabled() or not call_sid:
        return {"ok": False, "reason": "disabled"}
    upsert_call(
        call_sid=call_sid,
        direction=direction,
        business=business,
        slug=slug,
        phone=phone,
        ended_at=_now(),
        source="voice-agent",
    )
    result: dict[str, Any] = {"ok": True, "call_sid": call_sid}
    if turns is not None:
        saved = save_transcript(call_sid, turns, backend=backend, replace=True)
        result.update(saved)
    return result
