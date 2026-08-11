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
CUSTOMERS_PATH = os.getenv("CUSTOMERS_PATH", "/data/customers.json")
# Prefer in-cluster voice service (source of truth on voice PVC).
CUSTOMERS_API = (
    os.getenv("CUSTOMERS_API")
    or os.getenv("VOICE_API_BASE")
    or ""
).rstrip("/")
DESK_PUBLIC_HOST = os.getenv("DESK_PUBLIC_HOST", "sites.floridamanweb.online")
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
    custs = _load_customers_rows()
    url_to_phone = {}
    for c in custs:
        d_url = c.get("demo_url")
        ph = c.get("phone")
        if d_url and ph:
            url_to_phone[d_url.strip().rstrip("/")] = ph.strip()
    out = []
    for h, s in SITES.items():
        st = state.get(h)
        site_url = f"{BASE_URL}/{h}/"
        phone = url_to_phone.get(site_url.strip().rstrip("/"))
        if not phone:
            for c in custs:
                if c.get("slug") == h or c.get("business_name", "").lower() == s["business"].lower():
                    if c.get("phone"):
                        phone = c.get("phone")
                        break
        out.append({"hash": h, "business": s["business"], "title": s.get("title", ""),
                    "url": site_url,
                    "phone": phone,
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
    site_url = f"{BASE_URL}/{h}/"
    custs = _load_customers_rows()
    url_to_phone = {}
    for c in custs:
        d_url = c.get("demo_url")
        ph = c.get("phone")
        if d_url and ph:
            url_to_phone[d_url.strip().rstrip("/")] = ph.strip()
    phone = url_to_phone.get(site_url.strip().rstrip("/"))
    if not phone:
        for c in custs:
            if c.get("slug") == h or c.get("business_name", "").lower() == s["business"].lower():
                if c.get("phone"):
                    phone = c.get("phone")
                    break
    return {"hash": h, "business": s["business"], "title": s.get("title", ""),
            "url": site_url,
            "phone": phone,
            "status": st["status"] if st else "New", "notes": notes}


@app.post("/api/sites/{h}/notify")
def notify_site(h: str):
    import urllib.error
    import urllib.parse
    import urllib.request

    if h not in SITES:
        raise HTTPException(404, "site not found")
    s = SITES[h]
    site_url = f"{BASE_URL}/{h}/"

    custs = _load_customers_rows()
    url_to_phone = {}
    for c in custs:
        d_url = c.get("demo_url")
        ph = c.get("phone")
        if d_url and ph:
            url_to_phone[d_url.strip().rstrip("/")] = ph.strip()

    phone = url_to_phone.get(site_url.strip().rstrip("/"))
    if not phone:
        for c in custs:
            if c.get("slug") == h or c.get("business_name", "").lower() == s["business"].lower():
                if c.get("phone"):
                    phone = c.get("phone")
                    break

    if not phone:
        raise HTTPException(400, "no customer phone on file for this site")

    if not CUSTOMERS_API:
        raise HTTPException(500, "CUSTOMERS_API / VOICE_API_BASE not configured in site-tracker")

    target_url = f"{CUSTOMERS_API}/api/sms/notify-updated"
    payload = json.dumps({"phone": phone, "demo_url": site_url}).encode("utf-8")
    try:
        req = urllib.request.Request(
            target_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data
    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8", errors="ignore")
        raise HTTPException(he.code, f"voice-agent error: {err_body}")
    except Exception as e:
        raise HTTPException(500, f"could not reach customer registry: {e}")


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


def _load_customers_rows():
    """Prefer voice in-cluster API (customers.json lives on voice PVC)."""
    import urllib.error
    import urllib.request

    if CUSTOMERS_API:
        url = CUSTOMERS_API + "/api/onboarding/customers"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return list(data.get("customers") or [])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            pass
    path = CUSTOMERS_PATH
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.values()) if isinstance(data, dict) else []





@app.get("/api/customers")
def api_customers(status: str = ""):
    """Onboarding / sales funnel rows (voice registry via CUSTOMERS_API or file)."""
    try:
        rows = _load_customers_rows()
    except Exception as e:
        raise HTTPException(500, f"read customers: {e}")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"ok": True, "count": len(rows), "customers": rows, "desk": DESK_PUBLIC_HOST}


@app.get("/api/customers/{phone}")
def api_customer(phone: str):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        key = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        key = "+" + digits
    else:
        key = phone if str(phone).startswith("+") else "+" + digits
    rows = _load_customers_rows()
    row = next((r for r in rows if r.get("phone") == key), None)
    if not row:
        raise HTTPException(404, "customer not found")
    return {"ok": True, "customer": row, "builder_brief": row.get("builder_brief_path")}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))
