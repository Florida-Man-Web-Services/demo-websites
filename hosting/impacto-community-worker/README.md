# impacto.community Worker

Proxies `https://impacto.community/` → `https://floridamanweb.online/vanity/impacto/`.

The zone lives on Noah's Cloudflare account (NS `margot`/`martin`), **not** the
hwcopeland operator account (`cora`/`stan`). Do not add a `Zone` CR to IAC.

```bash
export CLOUDFLARE_API_TOKEN=...   # Zone DNS Edit + Workers Scripts/Routes Edit
npx wrangler deploy
```

DNS (proxied): apex + `www` CNAME/A so the Worker routes match. Originless
`192.0.2.0` is fine; the Worker never uses that origin.
