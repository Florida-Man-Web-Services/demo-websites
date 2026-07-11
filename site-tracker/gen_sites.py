"""Build-time: map each generated demo site to its content-hash path + business
name (from <title>), matching the demo-sites Dockerfile hashing exactly
(sha256(file bytes)[:12]). Writes sites.json next to app.py."""
import hashlib, html, json, os, re, sys

src = sys.argv[1] if len(sys.argv) > 1 else "generated-sites"
out = []
for fn in sorted(os.listdir(src)):
    if not fn.endswith(".html"):
        continue
    data = open(os.path.join(src, fn), "rb").read()
    h = hashlib.sha256(data).hexdigest()[:12]
    m = re.search(r"<title>(.*?)</title>", data.decode("utf-8", "ignore"), re.I | re.S)
    title = html.unescape(m.group(1).strip() if m else fn)
    business = title.split("|")[0].strip()                     # drop "| Gainesville, FL"
    business = re.sub(r"\s*[—–\-]\s*Gainesville,?\s*FL\.?\s*$", "", business, flags=re.I).strip()
    out.append({"hash": h, "business": business or title, "title": title, "file": fn})

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json"), "w") as f:
    json.dump(out, f)
print("wrote sites.json:", len(out), "sites")
