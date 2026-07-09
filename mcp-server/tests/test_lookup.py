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


def test_lookup_includes_maps_url_and_shared_demo_flag():
    result = lookup.find_business("Ole Barn")
    assert result["google_maps_url"].startswith("https://www.google.com/maps/")
    assert result["shared_demo"] is False


def test_lookup_shared_demo_true_for_shared_slug():
    import businesses
    shared = next((b for b in businesses.all_businesses() if b.shared_demo), None)
    assert shared is not None, "outreach-data.csv should have shared_demo=yes rows"
    assert lookup.find_business(shared.slug)["shared_demo"] is True
