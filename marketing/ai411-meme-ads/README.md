# Gainesville AI 411 — Meme Ad Campaign Pack

**Landing:** https://ai411.floridamanweb.online  
**Product:** Voice AI directory / events / people-QOTD / free demo website callbacks (Florida Man Web Services)

## What's in here

| Path | Contents |
|------|----------|
| `png/` | 16 still meme ads (feed, story, yellow-pages parody, tiles) |
| `copy/captions.md` | IG / X / TikTok / radio |
| `copy/grok-style-pack.md` | Spicier Grok-voice lines + Meta/Google RSA |
| `copy/campaign-pack.json` | Machine-readable pack |
| `ascii/ai411-banner.txt` | Terminal/banner art |
| `contact-sheet.jpg` | 8-up preview |
| `ai411-meme-reel.gif` | Slow slideshow reel |
| `index.html` | Local gallery (`python3 -m http.server` from this dir) |

## Tooling used

- **Pillow** — full static meme suite (Impact-style + stories + Yellow Pages parody)
- **Grok / xAI x_search** — meme format research (local lead-gen + call-this-number comedy)
- **Grok-voice copy pack** — spicy captions + RSA/Meta lines
- **ffmpeg** — GIF reel
- **ASCII** — banner
- **Flux 3 video** — unavailable (promotional period ended on Nous)
- **Google Workspace** — not authenticated on this Hermes profile (no Drive upload)
- **ComfyUI** — not launched (no local GPU path this session)

## Compliance note

Creative only. Outbound voice/SMS requires TCPA consent + DNC scrub (Twilio does **not** maintain National DNC). Prefer inbound AI 411 + web form callbacks.

## Quick preview

```bash
cd /home/noahtjones/demo-websites/marketing/ai411-meme-ads
python3 -m http.server 8765 --bind 127.0.0.1
# open http://127.0.0.1:8765/
```
