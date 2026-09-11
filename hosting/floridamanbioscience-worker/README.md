# floridamanbioscience.com Worker

Proxies `https://floridamanbioscience.com/` → `https://floridamanweb.online/vanity/florida-man-bioscience/`.

The zone lives on Noah's Cloudflare account (NS `margot`/`martin`), **not** the
hwcopeland operator account (`cora`/`stan`). Do not add a `Zone` CR to IAC.

Token from `~/.authinfo.gpg` (`machine cloudflare.com login apikey`). Never commit it.

DNS (proxied): apex originless `A 192.0.2.0` + `www` CNAME to apex. Bind zone Worker
routes, not account custom domains.
