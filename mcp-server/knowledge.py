"""Local business knowledge index over generated-sites/*.html (#47 MVP).

Indexes demo site HTML on demand: strip tags → text chunks with slug/title
metadata, score with in-process keyword/TF-IDF-style ranking (swappable later
for embeddings/vector search). No live crawl; file mtime is treated as
fetched_at for staleness.

Path is env-backed (KNOWLEDGE_DIR) so tests can point at fixtures.
"""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# Default: repo-root/generated-sites relative to this file (mcp-server/../generated-sites).
_DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "generated-sites"

# Chunk ~words; keep paragraphs together when possible.
_CHUNK_WORDS = 120
_CHUNK_OVERLAP_WORDS = 20

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)

# Common English stopwords — keep the set small; brand/category terms should score.
_STOPWORDS = frozenset(
    """
    a an the and or but if in on at to for of is are was were be been being
    this that these those it its with from by as we you your our their they
    he she them his her not no yes do does did doing have has had having
    will would can could should may might must shall
    about into over under out up down all any each few more most other some
    such than then so too very just also only own same
    """.split()
)

_index_lock = threading.Lock()
# Cache key: (resolved_dir, mtime signature) → Index
_index_cache: dict[tuple[str, str], "KnowledgeIndex"] = {}


def knowledge_dir() -> Path:
    """Directory of *.html business pages. Override with KNOWLEDGE_DIR."""
    env = os.getenv("KNOWLEDGE_DIR", "").strip()
    if env:
        return Path(env)
    return _DEFAULT_KNOWLEDGE_DIR


class _TextExtractor(HTMLParser):
    """Collect visible text + title from HTML; skip script/style/noscript."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._block_tags = frozenset(
            {
                "p",
                "div",
                "section",
                "article",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "li",
                "br",
                "tr",
                "header",
                "footer",
                "main",
                "nav",
                "aside",
                "blockquote",
                "pre",
                "ul",
                "ol",
                "table",
            }
        )

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
            return
        if t == "title":
            self._in_title = True
        if t in self._block_tags and self._skip_depth == 0:
            self.body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg") and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if t == "title":
            self._in_title = False
        if t in self._block_tags and self._skip_depth == 0:
            self.body_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.body_parts.append(text)
            self.body_parts.append(" ")


def strip_html(html: str) -> tuple[str, str]:
    """Return (title, body_text) from raw HTML."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed HTML — fall back to crude tag strip.
        title_m = re.search(
            r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S
        )
        title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
        no_script = re.sub(
            r"<(script|style|noscript)[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.I | re.S,
        )
        body = re.sub(r"<[^>]+>", " ", no_script)
        body = re.sub(r"\s+", " ", body).strip()
        return title, body
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
    body = re.sub(r"[ \t]+", " ", "".join(parser.body_parts))
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return title, body


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in _STOPWORDS and len(t) > 1]


def chunk_text(text: str, chunk_words: int = _CHUNK_WORDS) -> list[str]:
    """Split text into overlapping word windows; prefer paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0

    def flush() -> None:
        nonlocal buf, buf_words
        if not buf:
            return
        chunks.append(" ".join(buf).strip())
        if _CHUNK_OVERLAP_WORDS > 0 and buf_words > _CHUNK_OVERLAP_WORDS:
            # Keep tail words for overlap with next chunk.
            words = " ".join(buf).split()
            tail = words[-_CHUNK_OVERLAP_WORDS:]
            buf = [" ".join(tail)]
            buf_words = len(tail)
        else:
            buf = []
            buf_words = 0

    for para in paragraphs:
        words = para.split()
        if not words:
            continue
        # If paragraph alone is huge, hard-split by words.
        if len(words) > chunk_words * 2:
            if buf:
                flush()
            for i in range(0, len(words), chunk_words - _CHUNK_OVERLAP_WORDS):
                piece = words[i : i + chunk_words]
                if piece:
                    chunks.append(" ".join(piece))
            buf = []
            buf_words = 0
            continue
        if buf_words and buf_words + len(words) > chunk_words:
            flush()
        buf.append(para)
        buf_words += len(words)
        if buf_words >= chunk_words:
            flush()
    flush()
    return [c for c in chunks if c]


@dataclass
class Chunk:
    slug: str
    title: str
    text: str
    chunk_index: int
    fetched_at: str
    source_path: str
    tokens: list[str] = field(default_factory=list, repr=False)


@dataclass
class DocMeta:
    slug: str
    title: str
    fetched_at: str
    source_path: str
    word_count: int
    chunk_count: int
    preview: str


@dataclass
class KnowledgeIndex:
    dir_path: str
    chunks: list[Chunk]
    docs: dict[str, DocMeta]
    # term → document-frequency among chunks
    df: dict[str, int]
    n_chunks: int

    def score_query(self, query: str) -> list[tuple[float, Chunk]]:
        q_tokens = _content_tokens(query)
        if not q_tokens or not self.chunks:
            return []
        q_tf: dict[str, int] = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1
        q_unique = set(q_tf)
        n = max(self.n_chunks, 1)
        scored: list[tuple[float, Chunk]] = []
        for ch in self.chunks:
            if not ch.tokens:
                continue
            # TF in chunk
            c_tf: dict[str, int] = {}
            for t in ch.tokens:
                c_tf[t] = c_tf.get(t, 0) + 1
            score = 0.0
            # Title / slug boost for query terms
            title_toks = set(_content_tokens(ch.title + " " + ch.slug.replace("-", " ")))
            for term, qf in q_tf.items():
                if term not in c_tf and term not in title_toks:
                    continue
                idf = math.log((n + 1) / (self.df.get(term, 0) + 1)) + 1.0
                tf = c_tf.get(term, 0)
                # Sublinear TF
                tf_w = 1.0 + math.log(tf) if tf else 0.0
                score += qf * tf_w * idf
                if term in title_toks:
                    score += 2.0 * idf
            if score <= 0:
                continue
            # Soft coverage: reward matching more distinct query terms
            matched = len(q_unique & (set(c_tf) | title_toks))
            score *= 0.5 + 0.5 * (matched / max(len(q_unique), 1))
            scored.append((score, ch))
        scored.sort(key=lambda x: (-x[0], x[1].slug, x[1].chunk_index))
        return scored


def _mtime_signature(directory: Path) -> str:
    if not directory.is_dir():
        return "missing"
    parts: list[str] = []
    for p in sorted(directory.glob("*.html")):
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    return "|".join(parts) if parts else "empty"


def _fetched_at_iso(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _slug_from_path(path: Path) -> str:
    return path.stem


def build_index(directory: Path | None = None) -> KnowledgeIndex:
    """Build (or rebuild) an in-memory index of *.html under directory."""
    directory = Path(directory) if directory is not None else knowledge_dir()
    chunks: list[Chunk] = []
    docs: dict[str, DocMeta] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.html")):
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            title, body = strip_html(raw)
            slug = _slug_from_path(path)
            if not title:
                title = slug.replace("-", " ").title()
            fetched = _fetched_at_iso(path)
            source = str(path)
            text_chunks = chunk_text(body) or ([body] if body else [title])
            for i, piece in enumerate(text_chunks):
                toks = _content_tokens(piece)
                chunks.append(
                    Chunk(
                        slug=slug,
                        title=title,
                        text=piece,
                        chunk_index=i,
                        fetched_at=fetched,
                        source_path=source,
                        tokens=toks,
                    )
                )
            preview = (body or title)[:280].strip()
            docs[slug] = DocMeta(
                slug=slug,
                title=title,
                fetched_at=fetched,
                source_path=source,
                word_count=len((body or "").split()),
                chunk_count=len(text_chunks),
                preview=preview,
            )
    df: dict[str, int] = {}
    for ch in chunks:
        for term in set(ch.tokens):
            df[term] = df.get(term, 0) + 1
    return KnowledgeIndex(
        dir_path=str(directory.resolve()) if directory.exists() else str(directory),
        chunks=chunks,
        docs=docs,
        df=df,
        n_chunks=len(chunks),
    )


def get_index(directory: Path | None = None, *, force_reload: bool = False) -> KnowledgeIndex:
    """Return a cached index; reload when directory mtimes change."""
    directory = Path(directory) if directory is not None else knowledge_dir()
    key_dir = str(directory.resolve()) if directory.exists() else str(directory)
    sig = _mtime_signature(directory)
    cache_key = (key_dir, sig)
    with _index_lock:
        if not force_reload and cache_key in _index_cache:
            return _index_cache[cache_key]
        # Drop stale entries for this dir
        for k in list(_index_cache):
            if k[0] == key_dir:
                del _index_cache[k]
        idx = build_index(directory)
        _index_cache[cache_key] = idx
        return idx


def clear_index_cache() -> None:
    with _index_lock:
        _index_cache.clear()


def search_business_knowledge(query: str, limit: int = 5) -> dict:
    """Keyword/TF-IDF search over local demo-site knowledge chunks.

    Scoring is in-process and intentionally simple so it can be swapped for
    embeddings later without changing the tool contract.
    """
    try:
        q = (query or "").strip()
        if not q:
            return {
                "ok": False,
                "results": [],
                "error": "query is required — ask what the caller wants to know",
            }
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 5
        lim = max(1, min(lim, 20))

        directory = knowledge_dir()
        if not directory.is_dir():
            return {
                "ok": False,
                "results": [],
                "error": (
                    f"knowledge directory not found ({directory}) — "
                    "apologize and offer a callback via get_pitch_info"
                ),
            }

        idx = get_index(directory)
        if not idx.chunks:
            return {
                "ok": True,
                "query": q,
                "results": [],
                "indexed_docs": 0,
                "message": "no business pages indexed yet",
            }

        scored = idx.score_query(q)
        results = []
        for score, ch in scored[:lim]:
            snippet = ch.text
            if len(snippet) > 400:
                snippet = snippet[:397].rstrip() + "..."
            results.append(
                {
                    "slug": ch.slug,
                    "title": ch.title,
                    "snippet": snippet,
                    "score": round(score, 4),
                    "chunk_index": ch.chunk_index,
                    "fetched_at": ch.fetched_at,
                }
            )
        return {
            "ok": True,
            "query": q,
            "results": results,
            "indexed_docs": len(idx.docs),
            "scorer": "keyword-tfidf-v1",  # documented as swappable
        }
    except Exception as e:  # never raise — speakable error dict
        return {
            "ok": False,
            "results": [],
            "error": (
                f"knowledge search unavailable ({e.__class__.__name__}) — "
                "apologize and offer the owner's callback from get_pitch_info"
            ),
        }


def get_business_snapshot(slug: str) -> dict:
    """Return a compact snapshot of one business page by slug (filename stem)."""
    try:
        s = (slug or "").strip()
        if not s:
            return {
                "found": False,
                "error": "slug is required — use lookup_business first if you only have a name",
            }
        # Normalize common name-like input to slug form.
        s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()

        directory = knowledge_dir()
        if not directory.is_dir():
            return {
                "found": False,
                "error": (
                    f"knowledge directory not found ({directory}) — "
                    "apologize and offer a callback via get_pitch_info"
                ),
            }

        idx = get_index(directory)
        doc = idx.docs.get(s)
        if not doc:
            # Light fuzzy: prefix / substring among known slugs
            close = [
                d
                for d in idx.docs
                if s in d or d in s
            ][:5]
            return {
                "found": False,
                "slug": s,
                "suggestions": close,
                "error": f"no knowledge page for slug {s!r}",
            }

        # Full-ish body from joining chunks in order for this slug
        body_parts = [
            c.text
            for c in sorted(
                (c for c in idx.chunks if c.slug == s),
                key=lambda c: c.chunk_index,
            )
        ]
        full_text = "\n\n".join(body_parts)
        # Cap snapshot body for tool payloads
        max_chars = 3500
        truncated = len(full_text) > max_chars
        if truncated:
            full_text = full_text[: max_chars - 3].rstrip() + "..."

        return {
            "found": True,
            "slug": doc.slug,
            "title": doc.title,
            "fetched_at": doc.fetched_at,
            "word_count": doc.word_count,
            "chunk_count": doc.chunk_count,
            "preview": doc.preview,
            "text": full_text,
            "truncated": truncated,
            "source": "generated-sites",
        }
    except Exception as e:
        return {
            "found": False,
            "error": (
                f"knowledge snapshot unavailable ({e.__class__.__name__}) — "
                "apologize and offer the owner's callback from get_pitch_info"
            ),
        }
