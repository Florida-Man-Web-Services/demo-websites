"""Open a GitHub PR for an applied (shipped) ChangeRequest's site HTML.

After apply_change_request writes generated-sites/<slug>.html, call
open_site_update_pr(request_id) to branch, commit that single file, push, and
open a PR via `gh` or the GitHub REST API.

Safety defaults:
  SITE_PR_ENABLED=0  — do not push/create PR; return a dry-run plan only
  SITE_PR_AUTO=0     — apply_change_request does not auto-call this tool

Tests inject a GitBackend protocol so no real network or git push is used.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("demo-mcp.sitepr")

# Repo root = parent of mcp-server/ (where this file lives).
REPO_ROOT = Path(__file__).resolve().parent.parent

# Default relative path of the HTML file inside the repo.
SITE_REL_DIR = "generated-sites"

# Env knobs (read each call so tests can monkeypatch os.environ).
ENV_ENABLED = "SITE_PR_ENABLED"
ENV_AUTO = "SITE_PR_AUTO"
ENV_REPO = "SITE_PR_GITHUB_REPO"  # e.g. Florida-Man-Bioscience/demo-websites
ENV_BASE = "SITE_PR_BASE_BRANCH"  # default main


def site_pr_enabled() -> bool:
    """True only when SITE_PR_ENABLED is explicitly truthy (1/true/yes/on)."""
    v = (os.getenv(ENV_ENABLED) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def site_pr_auto() -> bool:
    """True only when SITE_PR_AUTO is explicitly truthy — optional apply hook."""
    v = (os.getenv(ENV_AUTO) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _repo_root() -> Path:
    env = os.getenv("SITE_PR_REPO_ROOT")
    if env:
        return Path(env).resolve()
    return Path(REPO_ROOT)


def _github_repo() -> str:
    return (
        os.getenv(ENV_REPO)
        or os.getenv("GITHUB_REPOSITORY")
        or "Florida-Man-Bioscience/demo-websites"
    ).strip()


def _default_base_branch() -> str:
    return (os.getenv(ENV_BASE) or "main").strip() or "main"


def _short_id(request_id: str) -> str:
    """Short token for branch names: strip cr- prefix, take first 8 hex-ish chars."""
    rid = (request_id or "").strip()
    if rid.startswith("cr-"):
        rid = rid[3:]
    rid = rid.replace("/", "-").replace("..", "")
    return (rid[:8] or "unknown").lower()


def branch_name_for(slug: str, request_id: str) -> str:
    safe_slug = (slug or "site").strip().replace("/", "-").replace("..", "")
    return f"site-update/{safe_slug}-{_short_id(request_id)}"


def commit_message_for(slug: str, request_id: str) -> str:
    return f"site({slug}): apply change request {request_id}"


def pr_title_for(slug: str, request_id: str) -> str:
    return f"site({slug}): apply change request {request_id}"


def pr_body_for(slug: str, request_id: str, summary: str = "") -> str:
    lines = [
        f"Applies ChangeRequest `{request_id}` to `generated-sites/{slug}.html`.",
        "",
        f"**Business:** `{slug}`",
        f"**Request:** `{request_id}`",
    ]
    if summary:
        lines.extend(["", f"**Summary:** {summary}"])
    lines.extend(
        [
            "",
            "Opened by `open_site_update_pr` (mcp-server site PR ship path, #52).",
        ]
    )
    return "\n".join(lines)


def site_rel_path(slug: str) -> str:
    s = (slug or "").strip()
    if s.endswith(".html"):
        s = s[: -len(".html")]
    return f"{SITE_REL_DIR}/{s}.html"


def _diff_stat(path: Path) -> dict[str, Any]:
    """Rough line-based size stats for speakable dry-run plans (no git required)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": f"could not read file ({e.__class__.__name__})"}
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    return {
        "path": path.name,
        "bytes": len(text.encode("utf-8")),
        "lines": lines,
    }


@runtime_checkable
class GitBackend(Protocol):
    """Protocol for branch+commit+PR. Tests inject mocks; prod uses SubprocessGitBackend."""

    def create_branch_commit_pr(
        self,
        *,
        repo_root: Path,
        base_branch: str,
        branch: str,
        rel_path: str,
        file_content: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        github_repo: str,
    ) -> dict[str, Any]:
        """Create branch from base, commit only rel_path, push, open PR.

        Returns speakable dict with at least:
          ok: bool
          branch: str
          pr_url: str | None
          error: optional
        """
        ...


class SubprocessGitBackend:
    """Git + gh (or REST) via subprocess, isolated in a temporary worktree."""

    def __init__(self, run_cmd: Any = None) -> None:
        # Injectable for tests: run_cmd(argv, **kwargs) -> CompletedProcess-like
        self._run = run_cmd or self._default_run

    @staticmethod
    def _default_run(
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        check: bool = False,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            check=check,
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=120,
        )

    def create_branch_commit_pr(
        self,
        *,
        repo_root: Path,
        base_branch: str,
        branch: str,
        rel_path: str,
        file_content: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        github_repo: str,
    ) -> dict[str, Any]:
        root = repo_root.resolve()
        if not (root / ".git").exists() and not (root / ".git").is_file():
            # Worktrees use .git as a file; still ok. Only fail if neither.
            git_dir = root / ".git"
            if not git_dir.exists():
                return {
                    "ok": False,
                    "error": f"not a git repo: {root}",
                    "branch": branch,
                }

        wt: Path | None = None
        try:
            # Prefer temporary worktree so production PVC checkout stays clean.
            wt_parent = Path(tempfile.mkdtemp(prefix="sitepr-wt-"))
            wt = wt_parent / "tree"
            # Fetch base if possible (best-effort).
            self._run(
                ["git", "fetch", "origin", base_branch],
                cwd=root,
                check=False,
            )
            add = self._run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-B",
                    branch,
                    str(wt),
                    f"origin/{base_branch}",
                ],
                cwd=root,
                check=False,
            )
            if add.returncode != 0:
                # Fallback: create from local base_branch
                add2 = self._run(
                    [
                        "git",
                        "worktree",
                        "add",
                        "-B",
                        branch,
                        str(wt),
                        base_branch,
                    ],
                    cwd=root,
                    check=False,
                )
                if add2.returncode != 0:
                    return {
                        "ok": False,
                        "error": (
                            "git worktree add failed: "
                            + ((add2.stderr or add.stderr or "")[:400])
                        ),
                        "branch": branch,
                    }

            target = wt / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file_content, encoding="utf-8")

            self._run(["git", "add", "--", rel_path], cwd=wt, check=False)
            commit = self._run(
                ["git", "commit", "-m", commit_message],
                cwd=wt,
                check=False,
            )
            if commit.returncode != 0:
                # Nothing to commit is still an error for our ship path.
                return {
                    "ok": False,
                    "error": (
                        "git commit failed: "
                        + ((commit.stderr or commit.stdout or "")[:400])
                    ),
                    "branch": branch,
                }

            push = self._run(
                ["git", "push", "-u", "origin", branch],
                cwd=wt,
                check=False,
            )
            if push.returncode != 0:
                return {
                    "ok": False,
                    "error": (
                        "git push failed: "
                        + ((push.stderr or push.stdout or "")[:400])
                    ),
                    "branch": branch,
                }

            pr_url = self._open_pr(
                cwd=wt,
                base_branch=base_branch,
                branch=branch,
                pr_title=pr_title,
                pr_body=pr_body,
                github_repo=github_repo,
            )
            if not pr_url:
                return {
                    "ok": False,
                    "error": "branch pushed but PR creation failed (gh/API)",
                    "branch": branch,
                    "pr_url": None,
                    "pushed": True,
                }
            return {
                "ok": True,
                "branch": branch,
                "pr_url": pr_url,
                "pushed": True,
                "commit_message": commit_message,
            }
        except Exception as e:  # speakable — never raise to MCP
            logger.exception("create_branch_commit_pr failed")
            return {
                "ok": False,
                "error": f"git/pr failed ({e.__class__.__name__}: {e})",
                "branch": branch,
            }
        finally:
            if wt is not None:
                try:
                    self._run(
                        ["git", "worktree", "remove", "--force", str(wt)],
                        cwd=root,
                        check=False,
                    )
                except Exception:
                    pass
                # Clean temp parent dir
                try:
                    parent = wt.parent
                    if parent.exists() and parent.name.startswith("sitepr-wt-"):
                        shutil.rmtree(parent, ignore_errors=True)
                except Exception:
                    pass

    def _open_pr(
        self,
        *,
        cwd: Path,
        base_branch: str,
        branch: str,
        pr_title: str,
        pr_body: str,
        github_repo: str,
    ) -> str | None:
        # Prefer gh CLI when available.
        if shutil.which("gh"):
            r = self._run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    github_repo,
                    "--base",
                    base_branch,
                    "--head",
                    branch,
                    "--title",
                    pr_title,
                    "--body",
                    pr_body,
                ],
                cwd=cwd,
                check=False,
            )
            if r.returncode == 0:
                url = (r.stdout or "").strip().splitlines()
                if url:
                    return url[-1].strip()
            # fall through to REST

        token = (
            os.getenv("GITHUB_TOKEN")
            or os.getenv("GH_TOKEN")
            or ""
        ).strip()
        if not token:
            logger.warning("no gh CLI success and no GITHUB_TOKEN/GH_TOKEN")
            return None

        # owner/repo
        parts = github_repo.split("/")
        if len(parts) != 2:
            return None
        owner, repo = parts
        payload = {
            "title": pr_title,
            "body": pr_body,
            "head": branch,
            "base": base_branch,
        }
        # Use curl via subprocess to avoid adding requests dependency.
        r = self._run(
            [
                "curl",
                "-sS",
                "-X",
                "POST",
                "-H",
                f"Authorization: Bearer {token}",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "Content-Type: application/json",
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                "-d",
                json.dumps(payload),
            ],
            cwd=cwd,
            check=False,
        )
        if r.returncode != 0:
            return None
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return data.get("html_url") or None


# Module-level backend (tests replace this).
_git_backend: GitBackend | None = None


def set_git_backend(backend: GitBackend | None) -> None:
    """Inject or clear the GitBackend (tests)."""
    global _git_backend
    _git_backend = backend


def get_git_backend() -> GitBackend:
    if _git_backend is not None:
        return _git_backend
    return SubprocessGitBackend()


def open_site_update_pr(
    request_id: str,
    *,
    dry_run: bool = False,
    base_branch: str = "main",
) -> dict[str, Any]:
    """Open (or plan) a GitHub PR for a shipped ChangeRequest's HTML file.

    dry_run=True always returns a plan without git push.
    When SITE_PR_ENABLED is false, behaves like dry_run even if dry_run=False
    (safe default for production MCP).
    """
    # Local import keeps module load light and avoids cycles.
    import changerequests as cr

    rid = (request_id or "").strip()
    if not rid:
        return {
            "ok": False,
            "opened": False,
            "error": "id is required",
        }

    loaded = cr.get_change_request(rid)
    if not loaded.get("found"):
        return {
            "ok": False,
            "opened": False,
            "error": loaded.get("error") or f"no change request with id {rid!r}",
            "id": rid,
        }

    req = loaded["request"]
    status = str(req.get("status") or "").lower()
    if status != "shipped":
        return {
            "ok": False,
            "opened": False,
            "id": rid,
            "status": status,
            "error": (
                f"request {rid} is {status or 'unknown'}; "
                "open_site_update_pr requires status=shipped "
                "(run apply_change_request first)"
            ),
        }

    # Idempotent: already has a PR
    existing_url = (req.get("pr_url") or "").strip()
    existing_branch = (req.get("pr_branch") or req.get("branch") or "").strip()
    if existing_url:
        return {
            "ok": True,
            "opened": True,
            "already_open": True,
            "id": rid,
            "pr_url": existing_url,
            "branch": existing_branch or None,
            "business_slug": req.get("business_slug"),
            "status": "shipped",
        }

    slug = str(req.get("business_slug") or "").strip()
    site_path, path_err = cr._site_path_for_slug(slug)
    if path_err or site_path is None:
        return {
            "ok": False,
            "opened": False,
            "id": rid,
            "business_slug": slug,
            "error": path_err or "invalid slug",
        }
    if not site_path.is_file():
        return {
            "ok": False,
            "opened": False,
            "id": rid,
            "business_slug": slug,
            "error": f"no site file for {slug!r} — apply_change_request first",
        }

    try:
        file_content = site_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {
            "ok": False,
            "opened": False,
            "id": rid,
            "error": f"could not read site file ({e.__class__.__name__})",
        }

    base = (base_branch or _default_base_branch()).strip() or "main"
    branch = branch_name_for(slug, rid)
    rel = site_rel_path(slug)
    commit_msg = commit_message_for(slug, rid)
    title = pr_title_for(slug, rid)
    body = pr_body_for(slug, rid, summary=str(req.get("summary") or ""))
    github_repo = _github_repo()
    stats = _diff_stat(site_path)

    plan = {
        "ok": True,
        "opened": False,
        "dry_run": True,
        "id": rid,
        "business_slug": slug,
        "branch": branch,
        "base_branch": base,
        "rel_path": rel,
        "commit_message": commit_msg,
        "pr_title": title,
        "github_repo": github_repo,
        "site_file": site_path.name,
        "diff_stat": stats,
        "note": (
            "Dry-run plan only — set SITE_PR_ENABLED=1 and call with dry_run=false "
            "to push branch and open PR."
        ),
    }

    # Force dry-run when feature flag is off or caller asked for dry_run.
    enabled = site_pr_enabled()
    if dry_run or not enabled:
        if not enabled and not dry_run:
            plan["note"] = (
                "SITE_PR_ENABLED is off (default) — returning plan without git push. "
                "Set SITE_PR_ENABLED=1 to actually open the PR."
            )
            plan["site_pr_enabled"] = False
        else:
            plan["site_pr_enabled"] = enabled
        plan["dry_run"] = True
        return plan

    backend = get_git_backend()
    result = backend.create_branch_commit_pr(
        repo_root=_repo_root(),
        base_branch=base,
        branch=branch,
        rel_path=rel,
        file_content=file_content,
        commit_message=commit_msg,
        pr_title=title,
        pr_body=body,
        github_repo=github_repo,
    )

    if not result.get("ok"):
        return {
            "ok": False,
            "opened": False,
            "id": rid,
            "business_slug": slug,
            "branch": branch,
            "error": result.get("error") or "git/pr backend failed",
            "pushed": bool(result.get("pushed")),
            "pr_url": result.get("pr_url"),
        }

    pr_url = result.get("pr_url") or ""
    # Persist on ChangeRequest record.
    fields: dict[str, Any] = {
        "pr_branch": branch,
        "branch": branch,
    }
    if pr_url:
        fields["pr_url"] = pr_url
        fields["pr_opened_at"] = cr._now_iso()
    updated = cr._update_request_fields(rid, fields)

    return {
        "ok": True,
        "opened": bool(pr_url),
        "dry_run": False,
        "id": rid,
        "business_slug": slug,
        "branch": branch,
        "base_branch": base,
        "pr_url": pr_url or None,
        "commit_message": commit_msg,
        "rel_path": rel,
        "github_repo": github_repo,
        "request": updated,
        "note": f"Opened PR for site update: {pr_url}" if pr_url else "Branch pushed.",
    }
