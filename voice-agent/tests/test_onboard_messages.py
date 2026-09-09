from onboard_messages import MSG_AI411, MSG_RESUME, register_message


def test_resume_source_message():
    assert register_message("resume_web") == MSG_RESUME
    assert "demo site" not in register_message("resume_web").lower()


def test_default_is_website_demo():
    assert register_message(None) == MSG_AI411
    assert register_message("ai411_web") == MSG_AI411
    assert register_message("") == MSG_AI411
