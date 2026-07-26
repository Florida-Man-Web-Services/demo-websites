#!/usr/bin/env python3
"""Fill the Gainesville demo-site standard prompt for one or more businesses.

Reads gainesville_no_website.json (or a path you pass), slugifies names the same
way voice-agent/businesses.py does, injects category design directions, and
prints a ready-to-send prompt (or writes files under --out-dir).

Examples:
  python3 docs/site-generation/fill_prompt.py --name "Bob's Barber Shop"
  python3 docs/site-generation/fill_prompt.py --slug bob-s-barber-shop --mode improve
  python3 docs/site-generation/fill_prompt.py --category barbershops --limit 3 --out-dir /tmp/prompts
  python3 docs/site-generation/fill_prompt.py --name "Ole Barn" --design-seed "neon dive, jukebox red"
  python3 docs/site-generation/fill_prompt.py --list-categories
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "gainesville-no-website" / "gainesville_no_website.json"
STANDARD_PROMPT = Path(__file__).resolve().parent / "STANDARD_PROMPT.md"
CATEGORY_DIR_MD = Path(__file__).resolve().parent / "category-directions.md"
GENERATED = ROOT / "generated-sites"

# search_category values in the JSON sometimes use "+" (nail+salons).
_CATEGORY_ALIASES = {
    "nail+salons": "nail salons",
    "nail salons": "nail salons",
    "hair salons": "hair salons",
    "tire shops": "tire shops",
    "print shops": "print shops",
    "tattoo shops": "tattoo shops",
    "convenience stores": "convenience stores",
    "grocery stores": "grocery stores",
    "clothing boutiques": "clothing boutiques",
    "cleaning services": "cleaning services",
    "dance studios": "dance studios",
    "dry cleaners": "dry cleaners",
    "martial arts": "martial arts",
    "pest control": "pest control",
    "food trucks": "food trucks",
    "auto repair": "auto repair",
    "car wash": "car wash",
    "lawn care": "lawn care",
    "insurance agents": "insurance agents",
}


def slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def load_businesses(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Expected list in {path}")
    return data


def extract_prompt_template(md_text: str) -> str:
    """Pull the fenced prompt block from STANDARD_PROMPT.md."""
    # First ``` ... ``` after "## Prompt"
    m = re.search(
        r"## Prompt \(copy from the line below\)\s*```\n(.*?)```",
        md_text,
        re.S,
    )
    if not m:
        # Fallback: largest fenced block
        blocks = re.findall(r"```\n(.*?)```", md_text, re.S)
        if not blocks:
            raise SystemExit("Could not find prompt template in STANDARD_PROMPT.md")
        return max(blocks, key=len).strip("\n")
    return m.group(1).strip("\n")


def parse_category_directions(md_text: str) -> dict[str, str]:
    """Map '## category' headings to their bodies."""
    parts = re.split(r"^## ", md_text, flags=re.M)
    out: dict[str, str] = {}
    for part in parts[1:]:
        lines = part.strip().splitlines()
        if not lines:
            continue
        key = lines[0].strip().lower()
        if key.startswith("fallback"):
            key = "fallback"
        body = "\n".join(lines[1:]).strip()
        out[key] = body
    return out


def normalize_category(raw: str) -> str:
    c = (raw or "").strip().lower()
    c = _CATEGORY_ALIASES.get(c, c)
    c = c.replace("+", " ")
    c = re.sub(r"\s+", " ", c).strip()
    return c


def category_direction(cat: str, directions: dict[str, str]) -> str:
    c = normalize_category(cat)
    if c in directions:
        return directions[c]
    # soft match: singular/plural
    for key, body in directions.items():
        if key == "fallback":
            continue
        if c.rstrip("s") == key.rstrip("s"):
            return body
    return directions.get(
        "fallback",
        "Invent a bold, coherent design system from the business name; keep NAP honest.",
    )


def fill(
    template: str,
    biz: dict,
    *,
    mode: str,
    hours: str,
    services: str,
    notes: str,
    design_seed: str,
    directions: dict[str, str],
) -> str:
    name = biz.get("name") or ""
    slug = slugify(name)
    search_cat = biz.get("search_category") or biz.get("category_label") or ""
    label = biz.get("category_label") or search_cat
    prior = GENERATED / f"{slug}.html"
    prior_path = str(prior) if prior.is_file() else ""
    if mode == "improve" and not prior_path:
        mode = "create"  # nothing to improve

    cat_block = category_direction(search_cat, directions)

    repl = {
        "{{MODE}}": mode,
        "{{NAME}}": name,
        "{{SLUG}}": slug,
        "{{SEARCH_CATEGORY}}": search_cat,
        "{{CATEGORY_LABEL}}": label,
        "{{ADDRESS}}": biz.get("address") or "(none on file)",
        "{{PHONE}}": biz.get("phone") or "(none on file)",
        "{{RATING}}": biz.get("rating") or "(none on file)",
        "{{GOOGLE_MAPS_URL}}": biz.get("google_maps_url") or "(none on file)",
        "{{HOURS}}": hours or "not provided",
        "{{SERVICES}}": services or "not provided",
        "{{NOTES}}": notes or "none",
        "{{DESIGN_SEED}}": design_seed or "none — invent a strong unique direction for this brand",
        "{{PRIOR_PATH}}": prior_path or "n/a",
        "{{CATEGORY_DIRECTION}}": cat_block,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help="Business list JSON (default: gainesville-no-website/…)",
    )
    ap.add_argument("--name", help="Exact or substring match on business name")
    ap.add_argument("--slug", help="Match slugify(name)")
    ap.add_argument("--category", help="Filter by search_category (substring)")
    ap.add_argument("--mode", choices=("create", "improve"), default="create")
    ap.add_argument("--hours", default="", help="Known hours text")
    ap.add_argument("--services", default="", help="Known services text")
    ap.add_argument("--notes", default="", help="Extra operator notes")
    ap.add_argument("--design-seed", default="", help="Aesthetic seed sentence(s)")
    ap.add_argument("--limit", type=int, default=1, help="Max businesses to emit")
    ap.add_argument(
        "--out-dir",
        type=Path,
        help="If set, write one .txt prompt per business instead of stdout",
    )
    ap.add_argument(
        "--list-categories",
        action="store_true",
        help="Print search_category values and exit",
    )
    ap.add_argument(
        "--ablation",
        action="store_true",
        help="Emit the short Coke-Zero-style ablation prompt instead of full standard",
    )
    args = ap.parse_args()

    businesses = load_businesses(args.json)

    if args.list_categories:
        cats = sorted(
            {
                (b.get("search_category") or b.get("category_label") or "").strip()
                for b in businesses
            }
        )
        for c in cats:
            if c:
                print(c)
        return

    selected: list[dict] = []
    for b in businesses:
        name = b.get("name") or ""
        slug = slugify(name)
        if args.name and args.name.lower() not in name.lower():
            continue
        if args.slug and args.slug != slug:
            continue
        if args.category:
            sc = (b.get("search_category") or b.get("category_label") or "").lower()
            if args.category.lower() not in sc:
                continue
        selected.append(b)
        if not (args.name or args.slug or args.category):
            # no filter: still require explicit selector for safety unless --limit with category
            pass

    if not (args.name or args.slug or args.category):
        ap.error("Provide --name, --slug, or --category to select businesses")

    if not selected:
        raise SystemExit("No businesses matched.")

    selected = selected[: max(1, args.limit)]

    template = extract_prompt_template(STANDARD_PROMPT.read_text(encoding="utf-8"))
    directions = parse_category_directions(
        CATEGORY_DIR_MD.read_text(encoding="utf-8")
    )

    ablation_tmpl = (
        "No skills are allowed. Create a beautiful landing page for {name} "
        "({label} in Gainesville, FL) using only plain AI. Address: {address}. "
        "Phone: {phone}. Rating: {rating}. It can use custom CSS only "
        "(no component frameworks). It must have at least five sections, with "
        "the hero section on top. Single self-contained HTML file. Do not invent "
        "phone, address, reviews, or testimonials. Write to generated-sites/{slug}.html."
    )

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    for i, biz in enumerate(selected):
        name = biz.get("name") or ""
        slug = slugify(name)
        if args.ablation:
            text = ablation_tmpl.format(
                name=name,
                label=biz.get("category_label") or biz.get("search_category") or "business",
                address=biz.get("address") or "(none)",
                phone=biz.get("phone") or "(none)",
                rating=biz.get("rating") or "(none)",
                slug=slug,
            )
        else:
            text = fill(
                template,
                biz,
                mode=args.mode,
                hours=args.hours,
                services=args.services,
                notes=args.notes,
                design_seed=args.design_seed,
                directions=directions,
            )

        header = (
            f"<!-- filled prompt for {name} | slug={slug} | mode={args.mode} -->\n\n"
        )
        payload = header + text + "\n"

        if args.out_dir:
            path = args.out_dir / f"{slug}.prompt.txt"
            path.write_text(payload, encoding="utf-8")
            print(f"wrote {path}", file=sys.stderr)
        else:
            if i > 0:
                print("\n" + "=" * 72 + "\n")
            print(payload, end="")


if __name__ == "__main__":
    main()
