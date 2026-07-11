import json

import pitch


def test_pitch_has_price_and_identity():
    p = pitch.get_pitch()
    assert p["business"] == "Florida Man Web Services"
    text = json.dumps(p)
    assert "$999" in text
    assert "per month" in text.lower()
    assert "one-time" not in text.lower()


def test_pitch_compliance_rules():
    p = pitch.get_pitch()
    joined = " ".join(p["compliance"]).lower()
    assert "ai" in joined
    assert "do-not-call" in joined or "do not call" in joined


def test_pitch_sms_caveat():
    assert "cannot" in pitch.get_pitch()["sms_caveat"].lower()


def test_pitch_callback_number_present(monkeypatch):
    import config
    monkeypatch.setattr(config, "OWNER_CALLBACK_NUMBER", "+13525550100")
    p = pitch.get_pitch()
    assert p["callback_number"] == "+13525550100"
    assert "+13525550100" in p["sms_caveat"]
