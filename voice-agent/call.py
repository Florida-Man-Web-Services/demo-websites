"""Place ONE outbound sales call, human-initiated.

    python call.py hayes-jewelry-ltd        # call a specific business by slug
    python call.py --next                   # call the highest-priority uncalled business
    python call.py --list                   # show the next 10 in the queue
    python call.py --to +13525551234 SLUG   # override the destination number (testing)

Deliberately NOT a batch dialer: each invocation places exactly one call that
you chose to make, which keeps a human in the loop for every dial (see
README.md > Compliance before calling anyone).
"""

import argparse
import csv
import sys

from twilio.rest import Client as TwilioClient

import config
from businesses import by_slug, call_queue, normalize_phone, slugify


def slug_from_row(row: dict) -> str:
    """Canonical slug for a call-order row.

    Derive it from the demo_url filename — that's the exact slug the site and
    outreach-data use, so it always resolves via by_slug. Falls back to
    slugify(name) only if demo_url is missing (which could mismatch or collide).
    """
    url = (row.get("demo_url") or "").rstrip("/")
    if url:
        stem = url.rsplit("/", 1)[-1]
        stem = stem[:-5] if stem.endswith(".html") else stem
        if stem:
            return stem
    return slugify(row["name"])


def already_called() -> set[str]:
    if not config.CALL_LOG.exists():
        return set()
    with open(config.CALL_LOG, newline="", encoding="utf-8") as f:
        return {
            row["slug"]
            for row in csv.DictReader(f)
            if not row.get("call_sid", "").startswith("TEST-")  # sims don't count
        }


def pick_next():
    done = already_called()
    for row in call_queue():
        if slug_from_row(row) not in done:
            return row
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", nargs="?", help="business slug (generated-sites filename)")
    parser.add_argument("--next", action="store_true", help="call next in priority queue")
    parser.add_argument("--list", action="store_true", help="show upcoming queue")
    parser.add_argument("--to", help="override destination number (E.164), for testing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    if args.list:
        done = already_called()
        shown = 0
        for row in call_queue():
            if slug_from_row(row) in done:
                continue
            print(f"{row['rank']:>4}. {row['name']}  {row['phone']}  ({row['category']})")
            shown += 1
            if shown == 10:
                break
        return

    row = None
    if args.next:
        row = pick_next()
        if row is None:
            sys.exit("Queue exhausted — every business in call-order.csv has a log entry.")
        slug = slug_from_row(row)
    elif args.slug:
        slug = args.slug
    else:
        parser.error("give a slug, --next, or --list")

    business = by_slug(slug)
    if business is None:
        sys.exit(f"No business found for slug {slug!r}")

    # Prefer an explicit --to, then the queue row's own phone (correct even when
    # two businesses share a slugged demo), then the resolved business phone.
    to = args.to or (row and row.get("phone")) or business.phone
    digits = normalize_phone(to)
    if len(digits) != 10:
        sys.exit(f"{business.name} has no usable phone number ({to!r})")
    to_e164 = f"+1{digits}"

    config.require(
        "ANTHROPIC_API_KEY", "DEEPINFRA_API_KEY", "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "PUBLIC_BASE_URL",
    )

    print(f"About to call {business.name} at {to_e164}")
    print(f"Demo: {business.demo_url}")
    if not args.yes and input("Place this call? [y/N] ").strip().lower() != "y":
        sys.exit("Aborted.")

    twilio = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    call = twilio.calls.create(
        to=to_e164,
        from_=config.TWILIO_PHONE_NUMBER,
        url=f"{config.PUBLIC_BASE_URL}/voice/outbound?slug={slug}",
        status_callback=f"{config.PUBLIC_BASE_URL}/voice/status",
        status_callback_event=["completed"],
    )
    print(f"Call placed: {call.sid} — watch the server logs for the conversation.")


if __name__ == "__main__":
    main()
