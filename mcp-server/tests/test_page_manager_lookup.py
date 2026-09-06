"""Page managers: customer CID resolves a generated-sites slug not in the catalog."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

MANAGER_PHONE = "+13214995975"
SLUG = "impacto-test-page"


@pytest.fixture
def page_manager(tmp_path, monkeypatch):
    sites = tmp_path / "generated-sites"
    sites.mkdir()
    (sites / f"{SLUG}.html").write_text(
        "<html><head><title>IMPACTO Test — Gainesville</title></head>"
        "<body><h1>IMPACTO Test</h1></body></html>",
        encoding="utf-8",
    )
    cust_path = tmp_path / "customers.json"
    cust_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(cust_path))
    monkeypatch.setenv("GENERATED_SITES_DIR", str(sites))

    import businesses
    import config
    import customers
    import lookup

    monkeypatch.setattr(config, "GENERATED_SITES_DIR", Path(sites))
    businesses._BUSINESSES = None
    importlib.reload(customers)
    yield {"customers": customers, "lookup": lookup, "businesses": businesses, "sites": sites}
    businesses._BUSINESSES = None
    importlib.reload(customers)


def test_generated_site_slug_not_in_catalog(page_manager):
    lookup = page_manager["lookup"]
    result = lookup.find_business(SLUG)
    assert result["found"] is True
    assert result["slug"] == SLUG
    assert "IMPACTO" in result["name"]
    assert result.get("phone") in ("", None)


def test_manager_phone_finds_claimed_slug(page_manager):
    customers = page_manager["customers"]
    lookup = page_manager["lookup"]
    r = customers.upsert(
        MANAGER_PHONE,
        status="active_owner",
        business_name="IMPACTO Test",
        contact_name="Nicolette",
        slug=SLUG,
        source="operator",
    )
    assert r["ok"] is True
    result = lookup.find_business("321 499 5975")
    assert result["found"] is True
    assert result["slug"] == SLUG
    assert result.get("owner_match") is True
    # Manager CID is not the public NAP.
    assert result.get("phone") in ("", None)


def test_stranger_phone_does_not_claim_generated_slug(page_manager):
    customers = page_manager["customers"]
    lookup = page_manager["lookup"]
    customers.upsert(
        MANAGER_PHONE,
        status="active_owner",
        slug=SLUG,
        business_name="IMPACTO Test",
    )
    result = lookup.find_business("+13525550199")
    assert result["found"] is False
    assert not result.get("owner_match")


def test_authorize_manager_write_on_claimed_slug(page_manager):
    customers = page_manager["customers"]
    customers.upsert(
        MANAGER_PHONE,
        status="active_owner",
        slug=SLUG,
        contact_name="Nicolette",
        business_name="IMPACTO Test",
    )
    ok = customers.authorize_owner_write(MANAGER_PHONE, SLUG)
    deny = customers.authorize_owner_write("+13525550199", SLUG)
    assert ok["ok"] is True
    assert deny["ok"] is False
    assert deny["code"] == "not_owner"


def test_entacto_asr_alias_finds_impacto(page_manager):
    sites = page_manager["sites"]
    (sites / "impacto.html").write_text(
        "<html><head><title>IMPACTO — Gainesville</title></head><body>IMPACTO</body></html>",
        encoding="utf-8",
    )
    lookup = page_manager["lookup"]
    result = lookup.find_business("Entacto")
    assert result["found"] is True
    assert result["slug"] == "impacto"
    assert "IMPACTO" in result["name"]
