"""CMS filesystem store: locking, CAS, crash isolation."""

from __future__ import annotations

import importlib

import pytest

import business_cms_schema as schema
import business_cms_store as store


@pytest.fixture
def cms_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    importlib.reload(store)
    return tmp_path / "cms"


def _doc(name="Cafe"):
    doc = schema.empty_document("cool-cafe", name)
    doc["identity"]["name"] = name
    doc["hours"]["unknown"] = True
    return doc


def test_disabled_store_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "false")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    importlib.reload(store)
    with pytest.raises(store.CmsStoreError) as exc:
        store.save_draft("cool-cafe", _doc(), expected_draft_rev=None, actor="t")
    assert exc.value.code == "feature_disabled"


def test_save_publish_restore_and_stale_conflict(cms_root):
    first = store.save_draft("cool-cafe", _doc("Cafe"), expected_draft_rev=None, actor="owner-1")
    assert first["ok"]
    rev = first["draft_revision"]
    with pytest.raises(store.CmsStoreError) as stale:
        store.save_draft("cool-cafe", _doc("Cafe 2"), expected_draft_rev=None, actor="owner-1")
    assert stale.value.code == "stale_draft"
    html = "<html><body>Cafe</body></html>"
    pub = store.publish(
        "cool-cafe",
        expected_draft_rev=rev,
        expected_release_id=None,
        actor="owner-1",
        html=html,
    )
    assert pub["ok"]
    live = store.current_release("cool-cafe")
    assert live is not None
    assert live["release_id"] == pub["release_id"]
    assert live["public"]["identity"]["name"] == "Cafe"
    restored = store.restore_to_draft("cool-cafe", pub["release_id"], actor="owner-1")
    assert restored["ok"]
    assert restored["draft_revision"] != rev
    versions = store.list_versions("cool-cafe")
    assert any(row["current"] for row in versions)


def test_crash_before_state_replace_does_not_publish(cms_root):
    store.save_draft("cool-cafe", _doc(), expected_draft_rev=None, actor="owner-1")
    orphan = store.write_unreferenced_release_for_crash_test("cool-cafe", _doc())
    live = store.current_release("cool-cafe")
    assert live is None
    assert (cms_root / "tenants" / "cool-cafe" / "releases" / orphan).is_dir()


def test_traversal_and_symlink_rejected(cms_root, tmp_path):
    with pytest.raises(schema.CmsValidationError):
        store.tenant_dir("../etc")
    outside = tmp_path / "outside"
    outside.mkdir()
    tenants = cms_root / "tenants"
    tenants.mkdir(parents=True)
    link = tenants / "evil"
    link.symlink_to(outside)
    with pytest.raises(store.CmsStoreError) as exc:
        store.tenant_dir("evil")
    assert exc.value.code in {"symlink_rejected", "path_escape"}


def test_blank_nap_survives_roundtrip(cms_root):
    saved = store.save_draft("cool-cafe", schema.empty_document("cool-cafe"), expected_draft_rev=None, actor="o")
    assert saved["document"]["identity"]["public_phone"] is None
    loaded = store.load_draft("cool-cafe")
    assert loaded is not None
    assert loaded["identity"]["address"] is None
