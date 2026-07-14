"""Polite, shallow website crawler → clean text.

Fetches a business homepage plus a few same-domain internal links, strips markup,
and returns concatenated visible text with a content hash (so unchanged sites are
skipped on the next run).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser


@dataclass
class SiteContent:
    url: str
    title: str
    text: str
    fetched_at: str
    content_hash: str


def _clean_text(html: str) -> tuple[str, str]:
    """Return (title, visible_text) using bs4 if present, else a regex fallback."""
    try:
        from bs4 import BeautifulSoup  # optional; regex fallback below
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()
        title = (soup.title.string or "").strip() if soup.title else ""
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return title, text
    except Exception:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
        stripped = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        return title, re.sub(r"\s+", " ", stripped).strip()


def _same_domain(a: str, b: str) -> bool:
    return urlparse(a).netloc.replace("www.", "") == urlparse(b).netloc.replace("www.", "")


def _robots_ok(url: str, ua: str, respect: bool) -> bool:
    if not respect:
        return True
    try:
        p = urlparse(url)
        rp = RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(ua, url)
    except Exception:
        return True  # if robots is unreachable, don't block


def fetch_site(url: str, cfg) -> Optional[SiteContent]:
    """Crawl homepage + up to (max_pages-1) internal links; return combined text."""
    import requests  # lazy import: only needed when actually crawling

    if not _robots_ok(url, cfg.user_agent, cfg.respect_robots):
        return None
    headers = {"User-Agent": cfg.user_agent}
    seen, texts, links_to_visit, title0 = set(), [], [url], ""

    while links_to_visit and len(seen) < cfg.max_pages_per_site:
        link = links_to_visit.pop(0)
        if link in seen:
            continue
        seen.add(link)
        try:
            r = requests.get(link, headers=headers, timeout=cfg.request_timeout)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
        except requests.RequestException:
            continue
        title, text = _clean_text(r.text)
        if link == url:
            title0 = title
        if text:
            texts.append(text)
        # discover a few more same-domain links from the homepage only
        if link == url:
            for m in re.findall(r'href=["\']([^"\']+)["\']', r.text):
                nxt = urljoin(url, m.split("#")[0])
                if nxt.startswith("http") and _same_domain(url, nxt) and nxt not in seen:
                    links_to_visit.append(nxt)

    if not texts:
        return None
    combined = " ".join(texts)[:200_000]  # cap enormous sites
    return SiteContent(
        url=url,
        title=title0,
        text=combined,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        content_hash=hashlib.sha256(combined.encode("utf-8", "ignore")).hexdigest(),
    )


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Character-window chunks with overlap (roughly ~250-300 tokens each)."""
    words = text.split()
    chunks, cur, cur_len = [], [], 0
    for w in words:
        cur.append(w)
        cur_len += len(w) + 1
        if cur_len >= size:
            chunks.append(" ".join(cur))
            keep = cur[-overlap:] if overlap < len(cur) else cur
            cur, cur_len = list(keep), sum(len(x) + 1 for x in keep)
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [text]
