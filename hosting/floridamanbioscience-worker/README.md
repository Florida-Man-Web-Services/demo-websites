# floridamanbioscience.com Worker

Proxies `https://floridamanbioscience.com/` → `https://flmanbiosci.net/`
(the live Next.js company site: full pages, team photos, product art).

Do **not** point this hostname at `floridamanweb.online/vanity/florida-man-bioscience/`
— that is the Gainesville agency demo pattern and is the wrong vehicle for FMB.

The zone lives on Noah's Cloudflare account (NS `margot`/`martin`), **not** the
hwcopeland operator account (`cora`/`stan`). Do not add a `Zone` CR to IAC.

Token from `~/.authinfo.gpg` (`machine cloudflare.com login apikey`). Never commit it.
