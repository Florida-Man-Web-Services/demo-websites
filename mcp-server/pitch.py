"""Static sales knowledge served by get_pitch_info — the agent's cheat sheet."""

import config


def get_pitch() -> dict:
    return {
        "business": "Florida Man Web Services",
        "owner": config.OWNER_NAME,
        "callback_number": config.OWNER_CALLBACK_NUMBER,
        "offer": {
            "demo": (
                "A free demo website is already built and live for every "
                "business we contact — no cost, no obligation to look."
            ),
            "price": (
                "Going live is $999 per month: their own domain name, "
                "professional setup and hosting, and their Google "
                "listing pointing at the new site."
            ),
            "keep_either_way": (
                "The demo is theirs to look at either way — if they change "
                "their mind later, the link will still be there."
            ),
        },
        "objections": {
            "not_interested": (
                "Totally understand — the demo is yours to keep either way, "
                "so if you ever change your mind the link will still be there."
            ),
            "how_much": (
                "The demo is completely free. Taking it live — real domain, "
                "hosting, Google finding you — is $999 a month. "
                "No hidden fees, no surprises."
            ),
            "send_to_email": (
                "Absolutely — what's the best email for you? "
                f"{config.OWNER_NAME} will send it over with all the details."
            ),
            "is_this_a_scam": (
                "Fair question — the demo is already built and free to look "
                f"at, and {config.OWNER_NAME} is a local Gainesville developer. "
                "There's nothing to pay unless you decide to go live."
            ),
        },
        "compliance": [
            "Identify yourself as an AI assistant in your first sentence.",
            "If they ask not to be contacted again, log outcome do_not_call "
            "and end the call — the do-not-call list is permanent.",
            "Log the call outcome exactly once before the call ends.",
        ],
        "sms_caveat": (
            "This assistant cannot send text messages. To share the demo "
            "link, read it out slowly, collect an email address, or offer "
            f"a callback from {config.OWNER_NAME} at "
            f"{config.OWNER_CALLBACK_NUMBER}."
        ),
    }
