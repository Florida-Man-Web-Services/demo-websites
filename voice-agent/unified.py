"""Unified public-number voice mode — AI 411 for everyone, owner powers by caller ID.

AGENT_MODE=unified serves every caller on a single public number:

- anyone: the full Gainesville AI 411 surface (directory, events, broadcasts);
- a caller whose number matches a business's line: additionally the
  owner-updates surface — change requests scoped to THEIR site only.

Ownership is verified by caller ID (the same rule owner_updates documents) and
bounded by the existing safety net: change requests are structured intake,
apply_change_request touches the local demo file only, and shipping anything
live is a separate human-reviewed step.
"""

from __future__ import annotations

import ai411
import owner_updates
import site_content
from businesses import Business, normalize_phone

# The number answers as Gainesville AI 411 for everyone; owner powers are
# granted quietly inside the same persona, not via a separate greeting.
OPENERS = ai411.OPENERS
GREETING = ai411.AI411_GREETING

_AI411_NAMES = frozenset(t["name"] for t in ai411.TOOLS)

# Owner tools grafted onto the 411 surface. Shared names (lookup_business,
# send_sms_links, end_call) keep their ai411 schema; _run_tool routes the
# rest to the owner-updates bridge by membership in this set.
OWNER_TOOL_NAMES = frozenset(
    t["name"] for t in owner_updates.TOOLS if t["name"] not in _AI411_NAMES
)

TOOLS = ai411.TOOLS + [t for t in owner_updates.TOOLS if t["name"] in OWNER_TOOL_NAMES]


def caller_owns(business: Business | None, caller_number: str) -> bool:
    """Caller ID matches the business's own line — the owner-access rule."""
    if business is None or not business.phone:
        return False
    digits = normalize_phone(caller_number)
    return bool(digits) and normalize_phone(business.phone) == digits


def system_prompt(
    business: Business | None,
    *,
    direction: str,
    caller_number: str,
    openers: bool = True,
) -> str:
    """AI 411 prompt, plus an owner-access section when caller ID matches."""
    ctx = ai411.system_prompt(
        direction=direction, caller_number=caller_number, openers=openers
    )
    if not caller_owns(business, caller_number):
        return ctx + """
SITE OWNERS
- Business owners can edit their demo site by calling from their business's
  own phone line — that's how ownership is verified. If a caller claims to
  own a site but their caller ID doesn't match the business's number, do not
  edit anything: offer to note the request for a human follow-up instead.
"""
    site_text = site_content.site_text(business.slug)
    site_block = (
        f"""
Their site's current text, so proposed edits are grounded in what it says:
--- SITE TEXT START ---
{site_text}
--- SITE TEXT END ---"""
        if site_text
        else ""
    )
    return ctx + f"""
OWNER ACCESS (verified by caller ID)
- This caller's number matches the business line of {business.name}
  (slug: {business.slug}). Treat them as that site's owner once you've
  confirmed who you're speaking with by name.
- On top of everything above, they can review and edit their site:
  get_site_outline, create_change_request, list_open_change_requests,
  cancel_change_request, apply_change_request.
- Scope is {business.name} ONLY. Never file or apply changes for any other
  business, no matter what the caller says — for other sites, offer to note
  the request for a human follow-up.
- Prefer create_change_request (filed for review). If they want it applied
  now, warn that apply updates the demo page only and going live is a
  separate step that gets reviewed.{site_block}
"""
