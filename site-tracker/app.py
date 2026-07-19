"""FMB Site Tracker — internal CRM for the floridamanweb.online demo sites.

Auth is handled UPSTREAM by Authentik forward-auth (the app is only reachable
through the guarded HTTPRoute). We just read the identity headers Authentik
injects (X-authentik-username / -email) to attribute status changes and notes.
"""
import os, re, json, sqlite3, datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SITES_JSON = os.getenv("SITES_JSON", os.path.join(APP_DIR, "sites.json"))
DB_PATH = os.getenv("TRACKER_DB", "/data/tracker.db")
BASE_URL = os.getenv("SITES_BASE_URL", "https://floridamanweb.online")
STATUSES = ["New", "Contacted", "Interested", "Sent", "Won", "Dead"]

with open(SITES_JSON) as f:
    SITES = {s["hash"]: s for s in json.load(f)}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS site_state ("
                  "hash TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'New', "
                  "updated_at TEXT, updated_by TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS notes ("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT NOT NULL, "
                  "author TEXT, body TEXT NOT NULL, created_at TEXT NOT NULL)")
        c.commit()


_init()
app = FastAPI(title="FMB Site Tracker")


def _user(request: Request) -> str:
    return (request.headers.get("x-authentik-username")
            or request.headers.get("x-authentik-email") or "unknown")


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


@app.get("/healthz")
def healthz():
    return {"ok": True, "sites": len(SITES)}


@app.get("/api/meta")
def meta(request: Request):
    return {"statuses": STATUSES, "base_url": BASE_URL, "user": _user(request)}


@app.get("/api/sites")
def list_sites():
    with _db() as c:
        state = {r["hash"]: r for r in c.execute("SELECT * FROM site_state")}
        ncount = {r["hash"]: r["n"] for r in
                  c.execute("SELECT hash, COUNT(*) n FROM notes GROUP BY hash")}
    out = []
    for h, s in SITES.items():
        st = state.get(h)
        out.append({"hash": h, "business": s["business"], "title": s.get("title", ""),
                    "url": f"{BASE_URL}/{h}/",
                    "status": st["status"] if st else "New",
                    "updated_at": st["updated_at"] if st else None,
                    "updated_by": st["updated_by"] if st else None,
                    "notes": ncount.get(h, 0)})
    out.sort(key=lambda x: x["business"].lower())
    return out


@app.get("/api/sites/{h}")
def site_detail(h: str):
    if h not in SITES:
        raise HTTPException(404)
    with _db() as c:
        st = c.execute("SELECT * FROM site_state WHERE hash=?", (h,)).fetchone()
        notes = [dict(r) for r in c.execute(
            "SELECT * FROM notes WHERE hash=? ORDER BY created_at DESC", (h,))]
    s = SITES[h]
    return {"hash": h, "business": s["business"], "title": s.get("title", ""),
            "url": f"{BASE_URL}/{h}/",
            "status": st["status"] if st else "New", "notes": notes}


class StatusIn(BaseModel):
    status: str


@app.post("/api/sites/{h}/status")
def set_status(h: str, body: StatusIn, request: Request):
    if h not in SITES:
        raise HTTPException(404)
    if body.status not in STATUSES:
        raise HTTPException(400, "bad status")
    with _db() as c:
        c.execute("INSERT INTO site_state(hash,status,updated_at,updated_by) "
                  "VALUES(?,?,?,?) ON CONFLICT(hash) DO UPDATE SET "
                  "status=excluded.status, updated_at=excluded.updated_at, "
                  "updated_by=excluded.updated_by",
                  (h, body.status, _now(), _user(request)))
        c.commit()
    return {"ok": True}


class NoteIn(BaseModel):
    body: str


@app.post("/api/sites/{h}/note")
def add_note(h: str, note: NoteIn, request: Request):
    if h not in SITES:
        raise HTTPException(404)
    body = note.body.strip()
    if not body:
        raise HTTPException(400, "empty note")
    with _db() as c:
        c.execute("INSERT INTO notes(hash,author,body,created_at) VALUES(?,?,?,?)",
                  (h, _user(request), body, _now()))
        c.commit()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))
