"""Relational call DB: outcomes on `calls`, turns on referenced transcript tables."""

import calldb
import config


def test_schema_and_transcript_reference(tmp_path, monkeypatch):
    db = tmp_path / "calls.db"
    csv_path = tmp_path / "call-log.csv"
    monkeypatch.setattr(config, "CALL_DB", db)
    monkeypatch.setattr(config, "CALL_LOG", csv_path)
    monkeypatch.setattr(config, "CALL_LOG_DUAL_WRITE_CSV", True)
    calldb._initialized_paths.clear()

    calldb.init_db()
    logged = calldb.log_outcome(
        call_sid="CA-test-1",
        direction="inbound",
        business="Ole Barn",
        slug="ole-barn",
        phone="+13525550199",
        outcome="interested",
        notes="Wants the demo live next week.",
        source="test",
    )
    assert logged["logged"] is True
    assert csv_path.exists()

    turns = [
        {"role": "agent", "content": "Hi, this is the web demo assistant."},
        {"role": "caller", "content": "Yeah, I'm interested."},
        {"role": "agent", "content": "Great — I'll log that."},
    ]
    saved = calldb.save_transcript("CA-test-1", turns, backend="pipeline")
    assert saved["turn_count"] == 3
    assert saved["transcript_ref"].startswith("calldb:transcript:")
    tid = saved["transcript_id"]

    row = calldb.get_call("CA-test-1")
    assert row is not None
    assert row["outcome"] == "interested"
    assert row["transcript_id"] == tid
    assert row["transcript_ref"] == f"calldb:transcript:{tid}"

    tr = calldb.get_transcript(call_sid="CA-test-1")
    assert tr is not None
    assert [t["role"] for t in tr["turns"]] == ["agent", "caller", "agent"]
    assert tr["turns"][1]["content"] == "Yeah, I'm interested."

    # Lookup by transcript id (the reference target).
    by_id = calldb.get_transcript(transcript_id=tid)
    assert by_id is not None
    assert by_id["call_sid"] == "CA-test-1"

    hist = calldb.history_for("ole-barn")
    assert len(hist) == 1
    assert hist[0]["transcript_ref"] == row["transcript_ref"]
    assert "Yeah" not in hist[0].get("notes", "")  # body not in summary row


def test_extract_pipeline_turns_skips_system_placeholders():
    messages = [
        {"role": "user", "content": "<call connected — greet them now>"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi there, is this the owner?"}],
        },
        {"role": "user", "content": "Yes speaking."},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Great."},
                {
                    "type": "tool_use",
                    "name": "log_call_outcome",
                    "input": {"outcome": "interested"},
                },
            ],
        },
    ]
    turns = calldb.extract_pipeline_turns(messages)
    roles = [t["role"] for t in turns]
    assert "caller" in roles
    assert turns[0]["role"] == "agent"
    assert "Hi there" in turns[0]["content"]
    assert any(t["role"] == "tool" for t in turns)


def test_finalize_call_without_prior_outcome(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    monkeypatch.setattr(config, "CALL_DB", db)
    monkeypatch.setattr(config, "CALL_LOG", tmp_path / "x.csv")
    monkeypatch.setattr(config, "CALL_LOG_DUAL_WRITE_CSV", False)
    calldb._initialized_paths.clear()

    result = calldb.finalize_call(
        "CA-orphan",
        [{"role": "agent", "content": "Goodbye"}],
        backend="grok-realtime",
        slug="salty-dog-saloon",
        business="Salty Dog",
    )
    assert result["ok"] is True
    assert result["turn_count"] == 1
    row = calldb.get_call("CA-orphan")
    assert row is not None
    assert row["slug"] == "salty-dog-saloon"
    assert row["transcript_ref"].startswith("calldb:transcript:")


def test_csv_migrate_on_empty_db(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text(
        "timestamp,call_sid,direction,business,slug,phone,outcome,email,callback_time,notes\n"
        "2026-01-01T12:00:00,CA-old,outbound,Ole Barn,ole-barn,+1,interested,,,hi\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CALL_DB", db)
    monkeypatch.setattr(config, "CALL_LOG", csv_path)
    calldb._initialized_paths.clear()
    calldb.init_db()
    migrated = calldb.get_call("CA-old")
    assert migrated is not None
    assert migrated["slug"] == "ole-barn"
    assert "ole-barn" in calldb.called_slugs()


def test_disabled_when_call_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALL_DB", None)
    calldb._initialized_paths.clear()
    assert calldb.enabled() is False
    assert calldb.log_outcome(
        call_sid="x",
        direction="",
        business="B",
        slug="b",
        phone="",
        outcome="other",
        dual_write_csv=False,
    )["call_id"] == 0
