"""Local-file tenant CMS store. Single-host, lock-and-rename publication.

Layout (under BUSINESS_CMS_DATA_DIR):
  tenants/<slug>/state.json
  tenants/<slug>/drafts/<revision>.json
  tenants/<slug>/releases/<release-id>/{cms.json,public.json,index.html}
  tenants/<slug>/inbox/<request-id>.json
  tenants/<slug>/.lock
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from business_cms_schema import (
    CmsValidationError,
    canonical_slug,
    public_projection,
    validate_document,
)

_ENV_ENABLED = "BUSINESS_CMS_ENABLED"
_ENV_DIR = "BUSINESS_CMS_DATA_DIR"


class CmsStoreError(RuntimeError):
    def __init__(self, code: str, message: str = "cms store error"):
        super().__init__(message)
        self.code = code


def cms_enabled() -> bool:
    return (os.getenv(_ENV_ENABLED) or "").strip().lower() in {"1", "true", "yes", "on"}


def data_dir() -> Path:
    raw = (os.getenv(_ENV_DIR) or "").strip()
    if not raw:
        raise CmsStoreError("data_dir_unset", "cms data directory is not configured")
    path = Path(raw).resolve()
    return path


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def tenant_dir(slug: str) -> Path:
    slug = canonical_slug(slug)
    root = data_dir()
    path = (root / "tenants" / slug).resolve()
    try:
        path.relative_to(root / "tenants")
    except ValueError as exc:
        raise CmsStoreError("path_escape", "slug path rejected") from exc
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if str(root) in str(parent)):
        raise CmsStoreError("symlink_rejected", "slug path rejected")
    return path


@contextmanager
def tenant_lock(slug: str) -> Iterator[Path]:
    if not cms_enabled():
        raise CmsStoreError("feature_disabled", "business cms is disabled")
    path = tenant_dir(slug)
    path.mkdir(parents=True, exist_ok=True)
    lock_path = path / ".lock"
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield path
    finally:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def empty_state(slug: str) -> dict[str, Any]:
    return {
        "slug": canonical_slug(slug),
        "draft_revision": None,
        "published_release": None,
        "audit": [],
    }


def load_state(slug: str) -> dict[str, Any]:
    path = tenant_dir(slug) / "state.json"
    if not path.is_file():
        return empty_state(slug)
    data = _read_json(path)
    if not isinstance(data, dict):
        raise CmsStoreError("corrupt_state", "state is unreadable")
    return data


def _audit_append(state: dict[str, Any], *, actor: str, action: str, target: str, outcome: str) -> None:
    entries = list(state.get("audit") or [])
    entries.append(
        {
            "at": _now(),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
        }
    )
    state["audit"] = entries[-200:]


def load_draft(slug: str) -> dict[str, Any] | None:
    state = load_state(slug)
    rev = state.get("draft_revision")
    if not rev:
        return None
    path = tenant_dir(slug) / "drafts" / f"{rev}.json"
    if not path.is_file():
        return None
    return _read_json(path)


def save_draft(
    slug: str,
    doc: dict[str, Any],
    *,
    expected_draft_rev: str | None,
    actor: str,
) -> dict[str, Any]:
    canonical = validate_document(doc, for_publish=False)
    if canonical["slug"] != canonical_slug(slug):
        raise CmsValidationError("slug mismatch")
    with tenant_lock(slug) as base:
        state = load_state(slug)
        current = state.get("draft_revision")
        if expected_draft_rev != current:
            raise CmsStoreError("stale_draft", "draft revision conflict")
        revision = _new_id("draft")
        _atomic_write(base / "drafts" / f"{revision}.json", canonical)
        state["draft_revision"] = revision
        _audit_append(state, actor=actor, action="save_draft", target=revision, outcome="ok")
        _atomic_write(base / "state.json", state)
        return {"ok": True, "draft_revision": revision, "document": canonical}


def current_release(slug: str) -> dict[str, Any] | None:
    state = load_state(slug)
    release_id = state.get("published_release")
    if not release_id:
        return None
    folder = tenant_dir(slug) / "releases" / str(release_id)
    cms_path = folder / "cms.json"
    if not cms_path.is_file():
        return None
    return {
        "release_id": release_id,
        "cms": _read_json(cms_path),
        "public": _read_json(folder / "public.json") if (folder / "public.json").is_file() else None,
        "html": (folder / "index.html").read_text(encoding="utf-8") if (folder / "index.html").is_file() else None,
        "folder": folder,
    }


def list_versions(slug: str) -> list[dict[str, Any]]:
    state = load_state(slug)
    base = tenant_dir(slug) / "releases"
    if not base.is_dir():
        return []
    rows = []
    current = state.get("published_release")
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        rows.append({"release_id": folder.name, "current": folder.name == current})
    return rows


def publish(
    slug: str,
    *,
    expected_draft_rev: str,
    expected_release_id: str | None,
    actor: str,
    html: str,
) -> dict[str, Any]:
    with tenant_lock(slug) as base:
        state = load_state(slug)
        if state.get("draft_revision") != expected_draft_rev:
            raise CmsStoreError("stale_draft", "draft revision conflict")
        if state.get("published_release") != expected_release_id:
            raise CmsStoreError("stale_release", "published revision conflict")
        draft_path = base / "drafts" / f"{expected_draft_rev}.json"
        if not draft_path.is_file():
            raise CmsStoreError("missing_draft", "draft is missing")
        cms = validate_document(_read_json(draft_path), for_publish=True)
        projection = public_projection(cms)
        release_id = _new_id("rel")
        folder = base / "releases" / release_id
        folder.mkdir(parents=True, exist_ok=False)
        _atomic_write(folder / "cms.json", cms)
        _atomic_write(folder / "public.json", projection)
        html_path = folder / "index.html"
        html_path.write_text(html, encoding="utf-8")
        with open(html_path, "ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        state["published_release"] = release_id
        _audit_append(state, actor=actor, action="publish", target=release_id, outcome="ok")
        _atomic_write(base / "state.json", state)
        return {
            "ok": True,
            "release_id": release_id,
            "draft_revision": expected_draft_rev,
            "public": projection,
        }


def restore_to_draft(slug: str, release_id: str, *, actor: str) -> dict[str, Any]:
    if not isinstance(release_id, str) or not release_id.startswith("rel-"):
        raise CmsStoreError("invalid_release", "release id is invalid")
    with tenant_lock(slug) as base:
        source = base / "releases" / release_id / "cms.json"
        if not source.is_file():
            raise CmsStoreError("missing_release", "release is missing")
        cms = validate_document(_read_json(source), for_publish=False)
        revision = _new_id("draft")
        _atomic_write(base / "drafts" / f"{revision}.json", cms)
        state = load_state(slug)
        state["draft_revision"] = revision
        _audit_append(state, actor=actor, action="restore", target=release_id, outcome="ok")
        _atomic_write(base / "state.json", state)
        return {"ok": True, "draft_revision": revision, "document": cms, "source_release": release_id}


def write_unreferenced_release_for_crash_test(slug: str, cms: dict[str, Any]) -> str:
    """Test helper: persist a release folder without advancing state.json."""
    canonical = validate_document(cms, for_publish=True)
    with tenant_lock(slug) as base:
        release_id = _new_id("rel")
        folder = base / "releases" / release_id
        folder.mkdir(parents=True, exist_ok=False)
        _atomic_write(folder / "cms.json", canonical)
        _atomic_write(folder / "public.json", public_projection(canonical))
        return release_id
