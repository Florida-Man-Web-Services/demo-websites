"""Setup doctor: the go-live walkthrough as live checks against .env.

Runs automatically when you enter the nix shell; run `python doctor.py`
any time. Prints what's configured, what's missing, and the exact next
command for each gap. Informational only — always exits 0.
"""

import config

OK = "\033[32m ok \033[0m"
TODO = "\033[33mTODO\033[0m"


def _unset(value: str) -> bool:
    """Treat empty and .env.example placeholder values ("xai-...", "AC...",
    "https://example.ngrok-free.app") as unconfigured."""
    return not value or "..." in value or "example." in value


def main() -> None:
    backend = config.VOICE_BACKEND
    print(f"— voice-agent doctor (VOICE_BACKEND={backend}, "
          f"LLM_PROVIDER={config.LLM_PROVIDER}) —")

    def step(done: bool, title: str, fix: str = "") -> bool:
        print(f"[{OK if done else TODO}] {title}")
        if not done and fix:
            print(f"        -> {fix}")
        return done

    # 1. Brain keys, per configured mode.
    if backend == "grok-realtime" or config.LLM_PROVIDER == "grok":
        brain = step(
            not _unset(config.XAI_API_KEY),
            "xAI API key (XAI_API_KEY)",
            "create at console.x.ai (needs credits), paste into voice-agent/.env",
        )
    else:
        brain = step(
            not _unset(config.ANTHROPIC_API_KEY),
            "Anthropic API key (ANTHROPIC_API_KEY)",
            "create at console.anthropic.com, paste into voice-agent/.env",
        )
    if backend == "pipeline":
        step(
            not _unset(config.DEEPINFRA_API_KEY),
            "DeepInfra key for Sesame TTS (needs funds on the account!)",
            "deepinfra.com dashboard -> Add Funds; or skip via VOICE_BACKEND=grok-realtime",
        )

    # 2. Twilio account.
    twilio = step(
        not (_unset(config.TWILIO_ACCOUNT_SID) or _unset(config.TWILIO_AUTH_TOKEN)
             or _unset(config.TWILIO_PHONE_NUMBER)),
        "Twilio SID + auth token + phone number",
        "console.twilio.com -> copy SID/token; buy a voice+SMS 352 number (~$1.15/mo)",
    )

    # 3. Public URL + webhooks.
    url = step(
        not _unset(config.PUBLIC_BASE_URL) and config.PUBLIC_BASE_URL.startswith("https://"),
        f"public HTTPS URL ({config.PUBLIC_BASE_URL or 'unset'})",
        "run: ngrok http 8035   (ngrok is in this shell) -> paste URL into PUBLIC_BASE_URL",
    )
    if url:
        print(f"        Twilio voice webhook:   {config.PUBLIC_BASE_URL}/voice/inbound (POST)")
        print(f"        Twilio SMS webhook:     {config.PUBLIC_BASE_URL}/sms/inbound (POST)")
        print(f"        Twilio status callback: {config.PUBLIC_BASE_URL}/voice/status")

    # 3b. Email (optional — demo link by email).
    import mailer
    step(
        mailer.email_configured(),
        "email backend (EMAIL_FROM + RESEND_API_KEY or SMTP_HOST)",
        "set EMAIL_FROM and RESEND_API_KEY (or SMTP_*) so send_demo_link_email works",
    )

    # 4. What to run next.
    print()
    if not brain:
        print("next: fill in the key above, then re-run: python doctor.py")
    elif backend == "grok-realtime":
        print("next: python realtime_chat.py <slug>     # text-mode smoke test, no phone")
        print("then: uvicorn server:app --port 8035     # + call your own cell first")
    else:
        print("next: python chat.py --list-slugs && python chat.py <slug>   # call simulator")
        print("then: uvicorn server:app --port 8035     # + call your own cell first")
    if not twilio:
        print("(phone calls stay blocked until the Twilio step is done)")


if __name__ == "__main__":
    main()
