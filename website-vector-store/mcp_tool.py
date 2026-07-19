"""FastMCP tool wrapper — exposes the Gainesville website index to the voice agent.

Register with the existing server (demo-websites/mcp-server/server.py):

    from website_vector_store.mcp_tool import register_wvs_tools
    register_wvs_tools(mcp)

so the agent can retrieve competitor/landscape context mid-call and surface
website-less prospects.
"""
from __future__ import annotations

from wvs.config import CONFIG
from wvs.embed import get_embedder
from wvs.store import VectorStore


def search_business_sites(query: str, k: int = 6) -> list[dict]:
    """Semantic search over indexed Gainesville business websites."""
    store = VectorStore(CONFIG.store_path)
    try:
        hits = store.query(get_embedder(CONFIG).embed([query])[0], k=k)
        return [{"business": h["name"], "url": h["url"], "score": round(h["score"], 3),
                 "snippet": h["text"][:240]} for h in hits]
    finally:
        store.close()


def website_prospects(limit: int = 25) -> list[dict]:
    """Gainesville businesses with no website — FMWS sales leads."""
    store = VectorStore(CONFIG.store_path)
    try:
        return store.prospects(limit)
    finally:
        store.close()


def register_wvs_tools(mcp) -> None:
    """Attach both tools to a FastMCP instance."""
    mcp.tool()(search_business_sites)
    mcp.tool()(website_prospects)
