"""Oxford proxy tests — no network, no real credentials."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture()
def oxford(monkeypatch):
    monkeypatch.setenv("OXFORD_APP_ID", "test-id")
    monkeypatch.setenv("OXFORD_APP_KEY", "test-key")
    monkeypatch.setenv("CUSTOMERS_PATH", "/tmp/oxford-test-customers.json")
    import oxford

    oxford._cache.clear()
    return oxford


def test_disabled_without_creds(oxford, monkeypatch):
    monkeypatch.delenv("OXFORD_APP_KEY")
    result = oxford.lookup("serendipity")
    assert result["ok"] is False and result["disabled"] is True


def test_invalid_input_rejected(oxford, monkeypatch):
    monkeypatch.delenv("OXFORD_APP_ID", raising=False)
    monkeypatch.delenv("OXFORD_APP_KEY", raising=False)
    assert oxford.lookup("").get("ok") is False
    assert oxford.lookup("a" * 80).get("ok") is False
    assert oxford.lookup("hi; DROP TABLE").get("ok") is False
    assert oxford.lookup("hi", lang="fr").get("ok") is False


def test_lookup_shapes_and_caches(oxford, monkeypatch):
    payload = {
        "results": [
            {
                "word": "serendipity",
                "lexicalEntries": [
                    {
                        "pronunciations": [{"phoneticSpelling": "sɛrənˈdɪpɪti"}],
                        "entries": [
                            {
                                "senses": [
                                    {
                                        "definitions": ["The occurrence of events by chance."],
                                        "examples": [{"text": "a lucky stroke of serendipity"}],
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ]
    }

    calls = []

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return payload

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        assert headers["app_id"] == "test-id" and headers["app_key"] == "test-key"
        return FakeResp()

    monkeypatch.setattr(oxford.httpx, "get", fake_get)
    first = oxford.lookup("Serendipity")
    assert first["ok"] is True and first["word"] == "serendipity"
    assert first["phonetic"] == "sɛrənˈdɪpɪti"
    assert "chance" in first["senses"][0]["definition"]
    assert first["cached"] is False
    second = oxford.lookup("serendipity")
    assert second["cached"] is True
    assert len(calls) == 1  # one upstream call only


def test_upstream_errors_never_echo_body(oxford, monkeypatch):
    class ErrResp:
        def __init__(self, code):
            self.status_code = code
            self.text = "<html>internal-secret-detail</html>"

        def json(self):  # pragma: no cover
            return {}

    for code in (401, 403, 429, 500):
        monkeypatch.setattr(
            oxford.httpx, "get", lambda *a, **k: ErrResp(code)
        )
        result = oxford.lookup("word")
        assert result["ok"] is False
        assert "internal-secret-detail" not in json.dumps(result)


def test_oxford_404_falls_back_to_public_dictionary(oxford, monkeypatch):
    class OxResp:
        status_code = 404
        text = '{"error":"No entry matches. Note: Sandbox environment."}'

        def json(self):
            return {"error": "No entry matches"}

    class FallbackResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "en": [
                    {
                        "partOfSpeech": "Noun",
                        "definitions": [
                            {
                                "definition": "The <a>occurrence</a> of events by chance.",
                                "examples": ["a fortunate stroke of serendipity"],
                            }
                        ],
                    }
                ]
            }

    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        if "oxforddictionaries.com" in url:
            return OxResp()
        return FallbackResp()

    monkeypatch.setattr(oxford.httpx, "get", fake_get)
    result = oxford.lookup("serendipity")
    assert result["ok"] is True
    assert result["word"] == "serendipity"
    assert "chance" in result["senses"][0]["definition"]
    assert "<a>" not in result["senses"][0]["definition"]
    assert any("oxforddictionaries.com" in u for u in calls)
    assert any("wiktionary.org" in u for u in calls)
    cached = oxford.lookup("serendipity")
    assert cached["cached"] is True
    assert len(calls) == 2  # oxford miss + one fallback; cache skips both


def test_entry_pronunciation_and_junk_senses_skipped(oxford, monkeypatch):
    class OxResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "results": [
                    {
                        "word": "apple",
                        "lexicalEntries": [
                            {
                                "entries": [
                                    {
                                        "pronunciations": [{"phoneticSpelling": "ˈap(ə)l"}],
                                        "senses": [
                                            {"definitions": ["ISO 639 language code for Apple."]},
                                            {
                                                "definitions": [
                                                    "the round fruit of a tree of the rose family"
                                                ]
                                            },
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }

    monkeypatch.setattr(oxford.httpx, "get", lambda *a, **k: OxResp())
    result = oxford.lookup("apple")
    assert result["phonetic"] == "ˈap(ə)l"
    assert result["source"] == "oxford"
    assert len(result["senses"]) == 1
    assert "fruit" in result["senses"][0]["definition"]


def test_fallback_404_is_word_not_found(oxford, monkeypatch):
    class Miss:
        status_code = 404
        text = "not found"

        def json(self):
            return {}

    monkeypatch.setattr(oxford.httpx, "get", lambda *a, **k: Miss())
    result = oxford.lookup("xyzzyplugh")
    assert result["ok"] is False
    assert result["error"] == "word not found"
