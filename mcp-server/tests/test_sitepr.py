"""Tests for open_site_update_pr / sitepr — mocked GitBackend, no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import changerequests as cr
import sitepr


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    store = tmp_path / "change-requests.jsonl"
    monkeypatch.setattr(cr, "CHANGE_REQUESTS_PATH", store)
    monkeypatch.delenv("CHANGE_REQUESTS_PATH", raising=False)
    return store


@pytest.fixture
def tiny_site(tmp_path, monkeypatch):
    sites = tmp_path / "generated-sites"
    sites.mkdir()
    html = """<!DOCTYPE html>
<html lang="en">
<head><title>Tiny Cafe</title></head>
<body>
  <h1>Welcome to Tiny Cafe</h1>
  <h2>Hours</h2>
  <p>Mon–Fri 9–5</p>
  <a href="tel:3525550000">(352) 555-0000</a>
</body>
</html>
"""
    (sites / "tiny-cafe.html").write_text(html, encoding="utf-8")
    monkeypatch.setattr(cr, "GENERATED_SITES_DIR", sites)
    monkeypatch.delenv("GENERATED_SITES_DIR", raising=False)
    return sites


@pytest.fixture
def shipped_request(tmp_store, tiny_site):
    created = cr.create_change_request(
        business_slug="tiny-cafe",
        summary="Update hours",
        items=[
            {
                "type": "hours",
                "before": "Mon–Fri 9–5",
                "after": "Mon–Sat 8am–8pm",
            }
        ],
    )
    rid = created["id"]
    applied = cr.apply_change_request(rid)
    assert applied["applied"] is True
    assert applied["status"] == "shipped"
    return rid


@pytest.fixture(autouse=True)
def _reset_backend(monkeypatch):
    """Clear injected backend + force SITE_PR_ENABLED off by default each test."""
    sitepr.set_git_backend(None)
    monkeypatch.delenv("SITE_PR_ENABLED", raising=False)
    monkeypatch.delenv("SITE_PR_AUTO", raising=False)
    yield
    sitepr.set_git_backend(None)


class RecordingBackend:
    """GitBackend mock that records create_branch_commit_pr calls."""

    def __init__(self, pr_url: str = "https://github.com/org/repo/pull/99") -> None:
        self.calls: list[dict[str, Any]] = []
        self.pr_url = pr_url
        self.should_fail = False
        self.fail_error = "mock failure"

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
        self.calls.append(
            {
                "repo_root": Path(repo_root),
                "base_branch": base_branch,
                "branch": branch,
                "rel_path": rel_path,
                "file_content": file_content,
                "commit_message": commit_message,
                "pr_title": pr_title,
                "pr_body": pr_body,
                "github_repo": github_repo,
            }
        )
        if self.should_fail:
            return {"ok": False, "error": self.fail_error, "branch": branch}
        return {
            "ok": True,
            "branch": branch,
            "pr_url": self.pr_url,
            "pushed": True,
            "commit_message": commit_message,
        }


def test_dry_run_plan_for_shipped_request(shipped_request, tiny_site, monkeypatch):
    backend = RecordingBackend()
    sitepr.set_git_backend(backend)

    result = sitepr.open_site_update_pr(shipped_request, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["opened"] is False
    assert result["business_slug"] == "tiny-cafe"
    assert result["branch"].startswith("site-update/tiny-cafe-")
    assert result["rel_path"] == "generated-sites/tiny-cafe.html"
    assert "apply change request" in result["commit_message"]
    assert shipped_request in result["commit_message"]
    assert result["diff_stat"]["lines"] > 0
    assert result["site_file"] == "tiny-cafe.html"
    # dry_run must never shell out / call backend
    assert backend.calls == []


def test_disabled_by_default_does_not_push(shipped_request, monkeypatch):
    backend = RecordingBackend()
    sitepr.set_git_backend(backend)
    # Ensure env is off
    monkeypatch.delenv("SITE_PR_ENABLED", raising=False)

    result = sitepr.open_site_update_pr(shipped_request, dry_run=False)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result.get("site_pr_enabled") is False
    assert backend.calls == []
    assert "SITE_PR_ENABLED" in (result.get("note") or "")


def test_git_backend_called_when_enabled(shipped_request, tiny_site, monkeypatch):
    backend = RecordingBackend(pr_url="https://github.com/Florida-Man-Bioscience/demo-websites/pull/42")
    sitepr.set_git_backend(backend)
    monkeypatch.setenv("SITE_PR_ENABLED", "1")

    result = sitepr.open_site_update_pr(shipped_request, dry_run=False, base_branch="main")

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["dry_run"] is False
    assert result["pr_url"].endswith("/pull/42")
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["rel_path"] == "generated-sites/tiny-cafe.html"
    assert call["base_branch"] == "main"
    assert call["branch"].startswith("site-update/tiny-cafe-")
    assert "tiny-cafe" in call["commit_message"]
    assert shipped_request in call["commit_message"]
    # Content should match applied HTML on disk
    disk = (tiny_site / "tiny-cafe.html").read_text(encoding="utf-8")
    assert call["file_content"] == disk
    assert "Mon–Sat 8am–8pm" in call["file_content"]

    # Fields persisted on ChangeRequest
    loaded = cr.get_change_request(shipped_request)
    assert loaded["found"] is True
    assert loaded["request"]["pr_url"] == result["pr_url"]
    assert loaded["request"]["pr_branch"] == result["branch"]


def test_already_open_idempotent(shipped_request, monkeypatch):
    backend = RecordingBackend()
    sitepr.set_git_backend(backend)
    monkeypatch.setenv("SITE_PR_ENABLED", "1")

    first = sitepr.open_site_update_pr(shipped_request, dry_run=False)
    assert first["opened"] is True
    assert len(backend.calls) == 1

    second = sitepr.open_site_update_pr(shipped_request, dry_run=False)
    assert second["ok"] is True
    assert second.get("already_open") is True
    assert second["pr_url"] == first["pr_url"]
    # Backend not called again
    assert len(backend.calls) == 1


def test_requires_shipped_status(tmp_store, tiny_site):
    created = cr.create_change_request(
        "tiny-cafe", "pending only", items=[{"type": "hours", "after": "x"}]
    )
    result = sitepr.open_site_update_pr(created["id"], dry_run=True)
    assert result["ok"] is False
    assert result["opened"] is False
    assert "shipped" in result["error"]


def test_missing_id():
    result = sitepr.open_site_update_pr("")
    assert result["ok"] is False
    assert "id" in result["error"]


def test_unknown_request(tmp_store):
    result = sitepr.open_site_update_pr("cr-doesnotexist")
    assert result["ok"] is False
    assert "no change request" in result["error"]


def test_branch_and_commit_helpers():
    assert sitepr.branch_name_for("ole-barn", "cr-abcdef123456").startswith(
        "site-update/ole-barn-abcdef12"
    )
    msg = sitepr.commit_message_for("ole-barn", "cr-abcdef123456")
    assert msg == "site(ole-barn): apply change request cr-abcdef123456"


def test_site_pr_enabled_flag(monkeypatch):
    monkeypatch.delenv("SITE_PR_ENABLED", raising=False)
    assert sitepr.site_pr_enabled() is False
    monkeypatch.setenv("SITE_PR_ENABLED", "1")
    assert sitepr.site_pr_enabled() is True
    monkeypatch.setenv("SITE_PR_ENABLED", "true")
    assert sitepr.site_pr_enabled() is True
    monkeypatch.setenv("SITE_PR_ENABLED", "0")
    assert sitepr.site_pr_enabled() is False


def test_backend_failure_speakable(shipped_request, monkeypatch):
    backend = RecordingBackend()
    backend.should_fail = True
    backend.fail_error = "push denied"
    sitepr.set_git_backend(backend)
    monkeypatch.setenv("SITE_PR_ENABLED", "1")

    result = sitepr.open_site_update_pr(shipped_request, dry_run=False)
    assert result["ok"] is False
    assert result["opened"] is False
    assert "push denied" in result["error"]
