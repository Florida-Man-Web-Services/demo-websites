# Demo MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An MCP server at `https://mcp.flmanbiosci.net/mcp` giving the xAI voice agent business lookups, pitch info, call history, and outcome logging for Florida Man Web Services.

**Architecture:** Python FastMCP (official `mcp` SDK) over Streamable HTTP on port 8036, importing `voice-agent/businesses.py`/`config.py` for single-source data access. Deployed as a container in the `theswamp` namespace on hwcopeland's RKE2 cluster via the existing ZOT + Flux pipeline.

**Tech Stack:** Python 3.12, `mcp` SDK (FastMCP), Starlette/uvicorn, pytest, Docker, Kubernetes (Flux, Cilium Gateway, Longhorn, External Secrets).

**Spec:** `docs/superpowers/specs/2026-07-09-demo-mcp-server-design.md`

## Global Constraints

- Price is **$999 flat one-time fee**; no monthly fees. Business name: **Florida Man Web Services**.
- Outcome enum must exactly match `voice-agent/agent.py`: `interested`, `wants_email`, `callback_requested`, `sent_sms`, `not_interested`, `do_not_call`, `wrong_number`, `voicemail`, `other`.
- `call-log.csv` columns exactly: `timestamp, call_sid, direction, business, slug, phone, outcome, email, callback_time, notes`. `call_sid` prefixed `XAI-`.
- Tools never raise into the transport — every failure returns a structured, speakable dict.
- No SMS capability. No Authentik forward-auth on the HTTPRoute. Bearer token auth in-app; `/health` unauthenticated.
- The repo-root `Dockerfile` (arcade-bar-south) must not be touched; the new image builds with `-f mcp-server/Dockerfile` and context `.`.
- Work happens in `/home/noahtjones/demo-websites` (tasks 1–7) and `/home/noahtjones/iac` (task 8).

---

### Task 1: Scaffold `mcp-server/` and test bootstrap

**Files:**
- Create: `mcp-server/requirements.txt`
- Create: `mcp-server/requirements-dev.txt`
- Create: `mcp-server/tests/conftest.py`
- Create: `mcp-server/.gitignore`

**Interfaces:**
- Produces: a `conftest.py` that puts both `mcp-server/` and `voice-agent/` on `sys.path` so every later test can `import businesses`, `import config`, `import calllog`, etc.

- [ ] **Step 1: Create the directory and requirements files**

`mcp-server/requirements.txt`:
```
mcp>=1.10
python-dotenv>=1.0
uvicorn>=0.30
```

`mcp-server/requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
httpx>=0.27
```

`mcp-server/.gitignore`:
```
.venv/
__pycache__/
```

- [ ] **Step 2: Write `mcp-server/tests/conftest.py`**

```python
"""Make mcp-server/ and voice-agent/ importable from tests."""
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = MCP_DIR.parent

sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(REPO_ROOT / "voice-agent"))
```

- [ ] **Step 3: Create a venv and install dev deps**

```bash
cd /home/noahtjones/demo-websites/mcp-server
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```
Expected: installs succeed; `.venv/bin/python -c "import mcp"` exits 0.

- [ ] **Step 4: Sanity-check the voice-agent import path**

```bash
cd /home/noahtjones/demo-websites/mcp-server
.venv/bin/python -c "import sys; sys.path.insert(0, '../voice-agent'); import businesses; print(len(businesses.all_businesses()))"
```
Expected: prints `252` (or close — the row count of outreach-data.csv).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/requirements.txt mcp-server/requirements-dev.txt mcp-server/tests/conftest.py mcp-server/.gitignore
git commit -m "mcp-server: scaffold requirements and test bootstrap"
```

---

### Task 2: `calllog.py` — history + append-only outcome logging

**Files:**
- Create: `mcp-server/calllog.py`
- Test: `mcp-server/tests/test_calllog.py`

**Interfaces:**
- Consumes: `config.CALL_LOG` (a `pathlib.Path`, monkeypatchable), `businesses.Business`.
- Produces:
  - `VALID_OUTCOMES: list[str]`
  - `history_for(slug: str) -> list[dict]` — rows from the call log matching that slug, oldest first.
  - `append_outcome(business, outcome, notes, email="", callback_time="") -> dict` — appends a row; returns `{"logged": True}` or `{"logged": False, "error": ..., "valid_outcomes": [...]}` for a bad outcome. `business` is a `businesses.Business`.

- [ ] **Step 1: Write the failing tests**

`mcp-server/tests/test_calllog.py`:
```python
import csv

import pytest

import businesses
import calllog
import config


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log = tmp_path / "call-log.csv"
    monkeypatch.setattr(config, "CALL_LOG", log)
    return log


def biz():
    return businesses.Business(name="Ole Barn", phone="352-555-0199")


def test_append_creates_header_and_row(tmp_log):
    result = calllog.append_outcome(biz(), "interested", "Loved the demo.")
    assert result == {"logged": True}
    rows = list(csv.DictReader(open(tmp_log, encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["business"] == "Ole Barn"
    assert rows[0]["slug"] == "ole-barn"
    assert rows[0]["outcome"] == "interested"
    assert rows[0]["call_sid"].startswith("XAI-")
    assert list(rows[0].keys()) == [
        "timestamp", "call_sid", "direction", "business", "slug",
        "phone", "outcome", "email", "callback_time", "notes",
    ]


def test_append_rejects_bad_outcome(tmp_log):
    result = calllog.append_outcome(biz(), "hung_up_angry", "notes")
    assert result["logged"] is False
    assert result["valid_outcomes"] == calllog.VALID_OUTCOMES
    assert not tmp_log.exists()


def test_history_matches_slug_only(tmp_log):
    calllog.append_outcome(biz(), "interested", "first call")
    calllog.append_outcome(
        businesses.Business(name="Salty Dog Saloon"), "voicemail", "left vm"
    )
    rows = calllog.history_for("ole-barn")
    assert len(rows) == 1
    assert rows[0]["notes"] == "first call"
    assert calllog.history_for("nobody-here") == []


def test_history_no_file(tmp_log):
    assert calllog.history_for("ole-barn") == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_calllog.py -v
```
Expected: FAIL / errors with `ModuleNotFoundError: No module named 'calllog'`.

- [ ] **Step 3: Write `mcp-server/calllog.py`**

```python
"""Append-only call-outcome logging + history reads for the MCP server.

Shares the CSV schema and outcome enum with voice-agent/agent.py so the
Twilio agent and the xAI agent write to interchangeable logs.
"""

import csv
import threading
from datetime import datetime

import config

# Must stay in sync with the log_call_outcome enum in voice-agent/agent.py.
VALID_OUTCOMES = [
    "interested",
    "wants_email",
    "callback_requested",
    "sent_sms",
    "not_interested",
    "do_not_call",
    "wrong_number",
    "voicemail",
    "other",
]

COLUMNS = [
    "timestamp", "call_sid", "direction", "business", "slug",
    "phone", "outcome", "email", "callback_time", "notes",
]

_write_lock = threading.Lock()


def history_for(slug: str) -> list[dict]:
    if not config.CALL_LOG.exists():
        return []
    with open(config.CALL_LOG, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("slug") == slug]


def append_outcome(
    business, outcome: str, notes: str, email: str = "", callback_time: str = ""
) -> dict:
    if outcome not in VALID_OUTCOMES:
        return {
            "logged": False,
            "error": f"invalid outcome {outcome!r}",
            "valid_outcomes": VALID_OUTCOMES,
        }
    now = datetime.now()
    with _write_lock:
        is_new = not config.CALL_LOG.exists()
        with open(config.CALL_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(COLUMNS)
            writer.writerow([
                now.isoformat(timespec="seconds"),
                f"XAI-{now.strftime('%Y%m%dT%H%M%S')}-{business.slug}",
                "xai",
                business.name,
                business.slug,
                business.phone,
                outcome,
                email,
                callback_time,
                notes,
            ])
    return {"logged": True}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_calllog.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/calllog.py mcp-server/tests/test_calllog.py
git commit -m "mcp-server: call-log history and append-only outcome logging"
```

---

### Task 3: `lookup.py` — business lookup with suggestions

**Files:**
- Create: `mcp-server/lookup.py`
- Test: `mcp-server/tests/test_lookup.py`

**Interfaces:**
- Consumes: `businesses.all_businesses()`, `businesses.by_slug()`, `businesses.by_phone()`, `businesses.slugify()`.
- Produces: `find_business(query: str) -> dict`. Hit: `{"found": True, "name", "slug", "category", "address", "phone", "rating", "demo_url"}`. Miss: `{"found": False, "suggestions": [{"name", "slug"}, ...]}` (≤3).

- [ ] **Step 1: Write the failing tests**

`mcp-server/tests/test_lookup.py`:
```python
import lookup


def test_lookup_by_exact_name():
    result = lookup.find_business("Ole Barn")
    assert result["found"] is True
    assert result["slug"] == "ole-barn"
    assert result["demo_url"].endswith("/ole-barn.html")
    assert result["address"]


def test_lookup_by_slug():
    assert lookup.find_business("salty-dog-saloon")["found"] is True


def test_lookup_by_phone():
    # Find any business with a phone, then look it up by that phone.
    import businesses
    with_phone = next(b for b in businesses.all_businesses() if b.phone)
    result = lookup.find_business(with_phone.phone)
    assert result["found"] is True
    assert result["slug"] == with_phone.slug


def test_lookup_miss_returns_suggestions():
    result = lookup.find_business("Ole Barne Saloon")
    assert result["found"] is False
    assert 1 <= len(result["suggestions"]) <= 3
    assert any(s["slug"] == "ole-barn" for s in result["suggestions"])


def test_lookup_hopeless_miss():
    result = lookup.find_business("zzzzqqqq")
    assert result["found"] is False
    assert result["suggestions"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_lookup.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'lookup'`.

- [ ] **Step 3: Write `mcp-server/lookup.py`**

```python
"""Resolve a caller's business by name, slug, or phone, with did-you-mean."""

import difflib

from businesses import all_businesses, by_phone, by_slug, slugify


def _profile(b) -> dict:
    return {
        "found": True,
        "name": b.name,
        "slug": b.slug,
        "category": b.category,
        "address": b.address,
        "phone": b.phone,
        "rating": b.rating,
        "demo_url": b.demo_url,
    }


def find_business(query: str) -> dict:
    q = (query or "").strip()
    digits = sum(ch.isdigit() for ch in q)
    if digits >= 7:  # looks like a phone number
        b = by_phone(q)
        if b:
            return _profile(b)
    b = by_slug(slugify(q))
    if b:
        return _profile(b)
    slugs = {x.slug: x for x in all_businesses()}
    close = difflib.get_close_matches(slugify(q), list(slugs), n=3, cutoff=0.5)
    return {
        "found": False,
        "suggestions": [{"name": slugs[s].name, "slug": s} for s in close],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_lookup.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/lookup.py mcp-server/tests/test_lookup.py
git commit -m "mcp-server: business lookup by name/slug/phone with suggestions"
```

---

### Task 4: `pitch.py` — static sales knowledge

**Files:**
- Create: `mcp-server/pitch.py`
- Test: `mcp-server/tests/test_pitch.py`

**Interfaces:**
- Consumes: `config.OWNER_NAME`, `config.OWNER_CALLBACK_NUMBER`.
- Produces: `get_pitch() -> dict` with keys `business`, `owner`, `callback_number`, `offer`, `objections`, `compliance`, `sms_caveat`.

- [ ] **Step 1: Write the failing tests**

`mcp-server/tests/test_pitch.py`:
```python
import json

import pitch


def test_pitch_has_price_and_identity():
    p = pitch.get_pitch()
    assert p["business"] == "Florida Man Web Services"
    text = json.dumps(p)
    assert "$999" in text
    assert "one-time" in text
    assert "no monthly" in text.lower()


def test_pitch_compliance_rules():
    p = pitch.get_pitch()
    joined = " ".join(p["compliance"]).lower()
    assert "ai" in joined
    assert "do-not-call" in joined or "do not call" in joined


def test_pitch_sms_caveat():
    assert "cannot" in pitch.get_pitch()["sms_caveat"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_pitch.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'pitch'`.

- [ ] **Step 3: Write `mcp-server/pitch.py`**

(Content distilled from `correspondences/phone-script.md`; price per the approved spec.)

```python
"""Static sales knowledge served by get_pitch_info — the agent's cheat sheet."""

import config


def get_pitch() -> dict:
    return {
        "business": "Florida Man Web Services",
        "owner": config.OWNER_NAME,
        "callback_number": config.OWNER_CALLBACK_NUMBER,
        "offer": {
            "demo": (
                "A free demo website is already built and live for every "
                "business we contact — no cost, no obligation to look."
            ),
            "price": (
                "Going live is a flat $999 one-time fee: their own domain "
                "name, professional setup and hosting, and their Google "
                "listing pointing at the new site. No monthly fees, ever."
            ),
            "keep_either_way": (
                "The demo is theirs to look at either way — if they change "
                "their mind later, the link will still be there."
            ),
        },
        "objections": {
            "not_interested": (
                "Totally understand — the demo is yours to keep either way, "
                "so if you ever change your mind the link will still be there."
            ),
            "how_much": (
                "The demo is completely free. Taking it live — real domain, "
                "hosting, Google finding you — is a flat $999 one time. "
                "No subscriptions, no surprises."
            ),
            "send_to_email": (
                "Absolutely — what's the best email for you? "
                f"{config.OWNER_NAME} will send it over with all the details."
            ),
            "is_this_a_scam": (
                "Fair question — the demo is already built and free to look "
                f"at, and {config.OWNER_NAME} is a local Gainesville developer. "
                "There's nothing to pay unless you decide to go live."
            ),
        },
        "compliance": [
            "Identify yourself as an AI assistant in your first sentence.",
            "If they ask not to be contacted again, log outcome do_not_call "
            "and end the call — the do-not-call list is permanent.",
            "Log the call outcome exactly once before the call ends.",
        ],
        "sms_caveat": (
            "This assistant cannot send text messages. To share the demo "
            "link, read it out slowly, collect an email address, or offer "
            f"a callback from {config.OWNER_NAME} at "
            f"{config.OWNER_CALLBACK_NUMBER}."
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_pitch.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/pitch.py mcp-server/tests/test_pitch.py
git commit -m "mcp-server: static pitch knowledge with $999 offer and compliance rules"
```

---

### Task 5: `server.py` — FastMCP tools, bearer auth, health

**Files:**
- Create: `mcp-server/server.py`
- Test: `mcp-server/tests/test_server.py`

**Interfaces:**
- Consumes: `find_business`, `get_pitch`, `history_for`, `append_outcome`, `VALID_OUTCOMES` from Tasks 2–4.
- Produces: module-level `app` (Starlette ASGI app; `/health` open, everything else bearer-gated) and `main()` entrypoint. MCP endpoint path: `/mcp`. Env: `MCP_AUTH_TOKEN` (required), `PORT` (default 8036).

- [ ] **Step 1: Write the failing tests**

`mcp-server/tests/test_server.py`:
```python
import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token-123")
    import server
    importlib.reload(server)
    with TestClient(server.build_app()) as c:
        yield c


def test_health_needs_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_mcp_rejects_missing_token(client):
    r = client.post("/mcp", json={})
    assert r.status_code == 401


def test_mcp_rejects_wrong_token(client):
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_mcp_accepts_right_token(client):
    # A garbage body with the right token must get past auth (not 401);
    # the MCP layer itself will reject the malformed request.
    r = client.post(
        "/mcp", json={}, headers={"Authorization": "Bearer test-token-123"}
    )
    assert r.status_code != 401


def test_tools_registered(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import server
    importlib.reload(server)
    import anyio
    tools = anyio.run(server.mcp.list_tools)
    names = {t.name for t in tools}
    assert names == {
        "lookup_business", "get_pitch_info", "get_call_history",
        "log_call_outcome",
    }


def test_log_tool_resolves_business(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "CALL_LOG", tmp_path / "log.csv")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import server
    importlib.reload(server)
    result = server.log_call_outcome("Ole Barn", "interested", "great call")
    assert result == {"logged": True}
    unknown = server.log_call_outcome("zzzzqqqq", "interested", "n")
    assert unknown["logged"] is False
    assert "suggestions" in unknown
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/test_server.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`.

- [ ] **Step 3: Write `mcp-server/server.py`**

```python
"""MCP server for Florida Man Web Services sales/support agents.

Streamable HTTP transport, bearer-token auth (except /health), four tools.
Run: MCP_AUTH_TOKEN=... python server.py   → http://0.0.0.0:8036/mcp
"""

import contextlib
import os
import sys
from pathlib import Path

# voice-agent/ holds the shared data layer (businesses.py, config.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from calllog import append_outcome, history_for
from lookup import find_business
from pitch import get_pitch

mcp = FastMCP(
    "florida-man-web-services", stateless_http=True, json_response=True
)


@mcp.tool()
def lookup_business(query: str) -> dict:
    """Look up a Gainesville business by name, slug, or phone number.

    Returns the business profile including its live demo website URL, or
    {"found": false, "suggestions": [...]} with close matches to offer the
    caller ("did you mean ...?").
    """
    try:
        return find_business(query)
    except Exception as e:  # keep failures speakable, never raise
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
def get_pitch_info() -> dict:
    """The Florida Man Web Services sales cheat sheet: the offer and price,
    objection-handling lines, compliance rules you must follow on every
    call, and what to do instead of texting (this server cannot send SMS).
    """
    return get_pitch()


@mcp.tool()
def get_call_history(business: str) -> dict:
    """Past call-log entries for a business (by name, slug, or phone), oldest
    first — check before pitching so you know prior contact and outcomes."""
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {"found": False, "suggestions": hit.get("suggestions", [])}
        return {"found": True, "slug": hit["slug"], "calls": history_for(hit["slug"])}
    except Exception as e:
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
def log_call_outcome(
    business: str,
    outcome: str,
    notes: str,
    email: str = "",
    callback_time: str = "",
) -> dict:
    """Record how the call went. Call exactly once near the end of every
    call. Outcomes: interested, wants_email, callback_requested, sent_sms,
    not_interested, do_not_call, wrong_number, voicemail, other. Use
    do_not_call whenever the person asks not to be contacted again."""
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {
                "logged": False,
                "error": f"unknown business {business!r}",
                "suggestions": hit.get("suggestions", []),
            }
        import businesses
        return append_outcome(
            businesses.by_slug(hit["slug"]), outcome, notes, email, callback_time
        )
    except Exception as e:
        return {"logged": False, "error": _unavailable(e)}


def _unavailable(e: Exception) -> str:
    return (
        f"data unavailable ({e.__class__.__name__}) — apologize and offer "
        "the owner's callback number from get_pitch_info"
    )


class BearerAuth:
    """401 everything except /health unless Authorization: Bearer matches."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            auth = next(
                (v.decode() for k, v in scope.get("headers", [])
                 if k == b"authorization"),
                "",
            )
            if not self.token or auth != f"Bearer {self.token}":
                await JSONResponse(
                    {"error": "unauthorized"}, status_code=401
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def health(request):
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    token = os.getenv("MCP_AUTH_TOKEN", "")
    inner = mcp.streamable_http_app()  # serves at /mcp within this sub-app

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=BearerAuth(inner, token)),
        ],
        lifespan=lifespan,
    )


def main():
    if not os.getenv("MCP_AUTH_TOKEN"):
        raise SystemExit("MCP_AUTH_TOKEN must be set")
    uvicorn.run(
        build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8036"))
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full suite**

```bash
cd /home/noahtjones/demo-websites/mcp-server && .venv/bin/python -m pytest tests/ -v
```
Expected: all tests pass (Tasks 2–5). If `test_mcp_accepts_right_token` fails because the SDK version routes differently, check `mcp.settings.streamable_http_path` — it must be `/mcp`.

- [ ] **Step 5: Manual smoke test with the MCP Inspector**

```bash
cd /home/noahtjones/demo-websites/mcp-server
MCP_AUTH_TOKEN=devtoken .venv/bin/python server.py &
npx @modelcontextprotocol/inspector --cli http://localhost:8036/mcp \
  --transport http --header "Authorization: Bearer devtoken" --method tools/list
kill %1
```
Expected: JSON listing the four tools. Also try `--method tools/call --tool-name lookup_business --tool-arg query="Ole Barn"` → profile with `demo_url`.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/server.py mcp-server/tests/test_server.py
git commit -m "mcp-server: FastMCP server with bearer auth, health, four tools"
```

---

### Task 6: Container image

**Files:**
- Create: `mcp-server/Dockerfile`
- Create: `mcp-server/.dockerignore` (note: repo root already has `Dockerfile` and `.dockerignore` for arcade-bar-south — do not touch them)

**Interfaces:**
- Produces: image running `server.py` on port 8036, data baked in, `CALL_LOG=/data/call-log.csv` (PVC mountpoint in Task 8).

- [ ] **Step 1: Write `mcp-server/Dockerfile`**

```dockerfile
# Build from the REPO ROOT: docker build -f mcp-server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY mcp-server/requirements.txt mcp-server/requirements.txt
RUN pip install --no-cache-dir -r mcp-server/requirements.txt

# Shared data layer + baked-in business data (refreshed on image rebuild).
COPY voice-agent/businesses.py voice-agent/config.py voice-agent/
COPY correspondences/outreach-data.csv correspondences/call-order.csv correspondences/
COPY gainesville-no-website/gainesville_no_website.json gainesville-no-website/
COPY mcp-server/*.py mcp-server/

# Call log lives on a mounted volume so outcomes survive restarts.
ENV CALL_LOG=/data/call-log.csv
RUN mkdir /data

EXPOSE 8036
CMD ["python", "mcp-server/server.py"]
```

- [ ] **Step 2: Build and smoke-test the container**

```bash
cd /home/noahtjones/demo-websites
docker build -f mcp-server/Dockerfile -t demo-mcp:local .
docker run --rm -d -p 8036:8036 -e MCP_AUTH_TOKEN=devtoken --name demo-mcp demo-mcp:local
sleep 2
curl -s http://localhost:8036/health          # expect: ok
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8036/mcp   # expect: 401
docker stop demo-mcp
```
Expected outputs as noted in the comments.

- [ ] **Step 3: Commit**

```bash
git add mcp-server/Dockerfile
git commit -m "mcp-server: container image (data baked in, call log on /data)"
```

---

### Task 7: CI — build and push to ZOT

**Files:**
- Create: `.github/workflows/build-demo-mcp.yml` (in demo-websites)

**Interfaces:**
- Produces: image `zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main` on every push to `main` touching the server or its data. Flux ImagePolicy (Task 8) tracks the `main` tag by digest.

- [ ] **Step 1: Write the workflow**

`.github/workflows/build-demo-mcp.yml`:
```yaml
---
name: Build demo-mcp

on:
  push:
    branches:
      - main
    paths:
      - 'mcp-server/**'
      - 'voice-agent/businesses.py'
      - 'voice-agent/config.py'
      - 'correspondences/outreach-data.csv'
      - 'correspondences/call-order.csv'
      - 'gainesville-no-website/gainesville_no_website.json'
      - '.github/workflows/build-demo-mcp.yml'
  workflow_dispatch:

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Zot registry
        uses: docker/login-action@v3
        with:
          registry: zot.hwcopeland.net
          username: ${{ secrets.ZOT_USERNAME }}
          password: ${{ secrets.ZOT_PASSWORD }}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: mcp-server/Dockerfile
          push: true
          tags: zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main
```

- [ ] **Step 2: Human step — repo secrets**

In GitHub → Florida-Man-Bioscience/demo-websites → Settings → Secrets and variables → Actions, add `ZOT_USERNAME` and `ZOT_PASSWORD` (the same ZOT service-account credentials the u4u repo uses — they're in the Vaultwarden item `766ec5c7-6aa8-419d-bb27-e5982872bc5b`, or ask hwcopeland).

- [ ] **Step 3: Commit, push, verify the run**

```bash
git add .github/workflows/build-demo-mcp.yml
git commit -m "ci: build demo-mcp image and push to ZOT"
git push origin main
gh run watch --repo Florida-Man-Bioscience/demo-websites
```
Expected: workflow green; `demo-mcp:main` visible in ZOT.

---

### Task 8: Cluster manifests in ~/iac (theswamp)

**Files (all under `/home/noahtjones/iac`):**
- Create: `rke2/tooling/flux/theswamp/pvc-mcp.yaml`
- Create: `rke2/tooling/flux/theswamp/external-secret-mcp.yaml`
- Create: `rke2/tooling/flux/theswamp/deployment-mcp.yaml`
- Create: `rke2/tooling/flux/theswamp/service-mcp.yaml`
- Create: `rke2/tooling/flux/theswamp/httproute-mcp.yaml`
- Modify: `rke2/tooling/flux/theswamp/kustomization.yaml` (append the five files)
- Create: `rke2/tooling/flux/image-automation/image-repository-demo-mcp.yaml`
- Create: `rke2/tooling/flux/image-automation/image-policy-demo-mcp.yaml`
- Modify: `rke2/tooling/flux/image-automation/kustomization.yaml` (append the two files)
- Modify: `rke2/kube-system/flmanbiosci-dnsrecord.yaml` (add `mcp` DNSRecord)

**Interfaces:**
- Consumes: image `zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main` (Task 7); container port 8036, `/health` probe, `MCP_AUTH_TOKEN` env, `/data` volume (Tasks 5–6).
- Produces: `https://mcp.flmanbiosci.net/mcp` live behind the gateway.

- [ ] **Step 1: Human step — Bitwarden item**

Generate the token and create the secret item:
```bash
openssl rand -hex 32
```
In Vaultwarden, create a **Login** item named `demo-mcp-auth` with `password` = that token. Copy the item's UUID — it replaces `REPLACE-WITH-BITWARDEN-UUID` in Step 2. (Keep the token; it also goes into the console.x.ai config in Task 9.)

- [ ] **Step 2: Write the theswamp manifests**

`rke2/tooling/flux/theswamp/pvc-mcp.yaml`:
```yaml
---
# Persists demo-mcp's call-log.csv across pod restarts.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: demo-mcp-data
  namespace: theswamp
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 1Gi
```

`rke2/tooling/flux/theswamp/external-secret-mcp.yaml`:
```yaml
---
# Bearer token the xAI agent presents to mcp.flmanbiosci.net.
# Bitwarden Login item "demo-mcp-auth"; the password field is the token.
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: demo-mcp-auth
  namespace: theswamp
spec:
  refreshInterval: "1h"
  secretStoreRef:
    kind: ClusterSecretStore
    name: bitwarden-login
  target:
    name: demo-mcp-auth
    creationPolicy: Owner
    template:
      engineVersion: v2
      data:
        MCP_AUTH_TOKEN: "{{ .password }}"
  data:
    - secretKey: password
      remoteRef:
        key: REPLACE-WITH-BITWARDEN-UUID
        property: password
```

`rke2/tooling/flux/theswamp/deployment-mcp.yaml`:
```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-mcp
  namespace: theswamp
spec:
  replicas: 1
  strategy:
    type: Recreate  # RWO volume — old pod must release it first
  selector:
    matchLabels:
      app: demo-mcp
  template:
    metadata:
      labels:
        app: demo-mcp
    spec:
      imagePullSecrets:
        - name: zot-pull-secret
      containers:
        - name: demo-mcp
          image: zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main # {"$imagepolicy": "tooling:demo-mcp"}
          ports:
            - containerPort: 8036
          env:
            - name: MCP_AUTH_TOKEN
              valueFrom:
                secretKeyRef:
                  name: demo-mcp-auth
                  key: MCP_AUTH_TOKEN
            - name: CALL_LOG
              value: /data/call-log.csv
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /health
              port: 8036
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8036
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "128Mi"
              cpu: "50m"
            limits:
              memory: "512Mi"
              cpu: "250m"
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: demo-mcp-data
```

`rke2/tooling/flux/theswamp/service-mcp.yaml`:
```yaml
---
apiVersion: v1
kind: Service
metadata:
  name: demo-mcp
  namespace: theswamp
spec:
  selector:
    app: demo-mcp
  ports:
    - port: 8036
      targetPort: 8036
```

`rke2/tooling/flux/theswamp/httproute-mcp.yaml`:
```yaml
---
# MCP endpoint for the xAI voice agent. Attaches to the *.flmanbiosci.net
# wildcard listener. Deliberately NO authentik.home.arpa/enabled annotation:
# the MCP client can't do OIDC — auth is a bearer token checked in-app.
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: demo-mcp
  namespace: theswamp
spec:
  parentRefs:
    - name: hwcopeland-gateway
      namespace: kube-system
      sectionName: flmanbiosci-https
  hostnames:
    - "mcp.flmanbiosci.net"
  rules:
    - backendRefs:
        - name: demo-mcp
          port: 8036
```

- [ ] **Step 3: Register the manifests in the kustomization**

In `rke2/tooling/flux/theswamp/kustomization.yaml`, append to `resources:`:
```yaml
  - pvc-mcp.yaml
  - external-secret-mcp.yaml
  - deployment-mcp.yaml
  - service-mcp.yaml
  - httproute-mcp.yaml
```

- [ ] **Step 4: Write the Flux image-automation manifests**

`rke2/tooling/flux/image-automation/image-repository-demo-mcp.yaml`:
```yaml
---
apiVersion: image.toolkit.fluxcd.io/v1beta2
kind: ImageRepository
metadata:
  name: demo-mcp
  namespace: tooling
spec:
  image: zot.hwcopeland.net/florida-man-bioscience/demo-mcp
  interval: 1m0s
  secretRef:
    name: zot-pull-secret
```

`rke2/tooling/flux/image-automation/image-policy-demo-mcp.yaml`:
```yaml
---
# Same digest-reflection pattern as u4u-engine: the "main" tag is stable,
# so track the digest behind it.
apiVersion: image.toolkit.fluxcd.io/v1beta2
kind: ImagePolicy
metadata:
  name: demo-mcp
  namespace: tooling
spec:
  imageRepositoryRef:
    name: demo-mcp
  filterTags:
    pattern: '^main$'
  policy:
    alphabetical:
      order: asc
  digestReflectionPolicy: Always
  interval: 1m0s
```

In `rke2/tooling/flux/image-automation/kustomization.yaml`, append to `resources:`:
```yaml
  - image-repository-demo-mcp.yaml
  - image-policy-demo-mcp.yaml
```
(The existing `ImageUpdateAutomation` named `u4u-engine` already rewrites the whole `./rke2/tooling/flux/theswamp` path with the Setters strategy, so the `$imagepolicy` marker in `deployment-mcp.yaml` needs no new automation object.)

- [ ] **Step 5: Add the DNS record**

Append to `rke2/kube-system/flmanbiosci-dnsrecord.yaml` (same edge IP as the other flmanbiosci hosts):
```yaml
---
# mcp -> same edge IP as the apex; MCP endpoint for AI sales agents
# (rke2/tooling/flux/theswamp/httproute-mcp.yaml).
apiVersion: cloudflare-operator.io/v1
kind: DNSRecord
metadata:
  name: flmanbiosci-mcp
  namespace: kube-system
spec:
  name: mcp.flmanbiosci.net
  type: A
  content: 69.180.240.158
  proxied: true
  ttl: 1
  interval: 5m
```
Note: `kube-system` is outside the `theswamp` RBAC scope. If Flux doesn't reconcile `rke2/kube-system/`, this one file needs hwcopeland (or someone with cluster-admin) to `kubectl apply -f` it — call that out in the commit/PR message.

- [ ] **Step 6: Commit ~/iac on a branch and open a PR**

```bash
cd /home/noahtjones/iac
git checkout -b demo-mcp
git add rke2/tooling/flux/theswamp/ rke2/tooling/flux/image-automation/ rke2/kube-system/flmanbiosci-dnsrecord.yaml
git commit -m "theswamp: add demo-mcp MCP server (mcp.flmanbiosci.net) for xAI sales agent"
git push -u origin demo-mcp
gh pr create --title "theswamp: demo-mcp MCP server" --body "MCP endpoint for the FMWS xAI voice agent. Needs: (1) merge, (2) kube-system DNSRecord applied if Flux doesn't cover rke2/kube-system/."
```
(A PR rather than direct push to main — Flux deploys straight from main, and the DNS record touches kube-system, so hwcopeland should get eyes on it.)

- [ ] **Step 7: Verify after merge**

```bash
kubectl -n theswamp get pods -l app=demo-mcp        # Running
kubectl -n theswamp get externalsecret demo-mcp-auth # READY True
curl -s https://mcp.flmanbiosci.net/health           # ok
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.flmanbiosci.net/mcp  # 401
```

---

### Task 9: Wire up console.x.ai + README

**Files:**
- Create: `mcp-server/README.md`

- [ ] **Step 1: Write `mcp-server/README.md`**

```markdown
# demo-mcp — MCP server for Florida Man Web Services agents

Gives AI sales/support agents (currently the xAI voice agent) business
lookups, the sales pitch, call history, and outcome logging for the
Gainesville demo-websites campaign.

## Endpoint

- URL: `https://mcp.flmanbiosci.net/mcp` (Streamable HTTP)
- Auth: `Authorization: Bearer <MCP_AUTH_TOKEN>` — token lives in the
  Vaultwarden item `demo-mcp-auth`
- Health: `GET /health` (no auth)

## Tools

| Tool | Purpose |
| --- | --- |
| `lookup_business(query)` | Profile + demo URL by name/slug/phone; suggestions on miss |
| `get_pitch_info()` | Offer ($999 one-time), objections, compliance rules |
| `get_call_history(business)` | Prior call-log rows for the business |
| `log_call_outcome(business, outcome, notes, email?, callback_time?)` | Append to call-log.csv |

## Local development

    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest tests/ -v
    MCP_AUTH_TOKEN=devtoken .venv/bin/python server.py   # http://localhost:8036/mcp

## Deployment

Push to `main` → GitHub Actions builds
`zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main` → Flux image
automation rolls out the `demo-mcp` Deployment in `theswamp`
(manifests: iac repo, `rke2/tooling/flux/theswamp/*-mcp.yaml`).
The call log persists on the `demo-mcp-data` PVC at `/data/call-log.csv`.
```

- [ ] **Step 2: Human step — console.x.ai**

In the xAI agent's tool/MCP configuration: add an MCP server with URL `https://mcp.flmanbiosci.net/mcp`, transport **Streamable HTTP** (sometimes labeled just "HTTP"), and a custom header `Authorization: Bearer <token from the demo-mcp-auth Vaultwarden item>`. Then instruct the agent (system prompt) to call `get_pitch_info` at call start and `log_call_outcome` before hanging up.

- [ ] **Step 3: End-to-end verification**

Ask the xAI agent (test call or console playground): "What's the demo site for Ole Barn and how much does going live cost?" Expected: it calls `lookup_business` + `get_pitch_info` and answers with the ole-barn demo URL and $999 one-time. Then confirm a new `XAI-` row lands in the pod's call log:
```bash
kubectl -n theswamp exec deploy/demo-mcp -- tail -3 /data/call-log.csv
```

- [ ] **Step 4: Commit**

```bash
git add mcp-server/README.md
git commit -m "mcp-server: README with endpoint, tools, and deploy docs"
git push origin main
```
