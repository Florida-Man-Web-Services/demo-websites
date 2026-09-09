MSG_AI411 = (
    "You are on the list — we will call shortly to design your free demo site."
)
MSG_RESUME = (
    "You are on the waitlist — we will call about Resume & Job Application Assistant."
)


def register_message(source: str | None) -> str:
    src = (source or "ai411_web").strip() or "ai411_web"
    if src == "resume_web":
        return MSG_RESUME
    return MSG_AI411
