/**
 * Apex Worker for floridamanbioscience.com — reverse-proxy the live FMB
 * Next.js site (flmanbiosci.net). Not the Gainesville demo-sites vanity HTML.
 * Host header to origin is flmanbiosci.net so the existing gateway matches.
 */
const HOP = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "cf-connecting-ip",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "cf-ew-via",
  "cdn-loop",
]);

function originUrl(request, originBase) {
  const incoming = new URL(request.url);
  const base = originBase.replace(/\/$/, "");
  const path = incoming.pathname === "/" ? "/" : incoming.pathname;
  const dest = new URL(base + path);
  dest.search = incoming.search;
  return dest;
}

function forwardHeaders(src) {
  const headers = new Headers();
  for (const [k, v] of src.entries()) {
    if (!HOP.has(k.toLowerCase())) headers.set(k, v);
  }
  return headers;
}

export default {
  async fetch(request, env) {
    const dest = originUrl(request, env.ORIGIN_BASE);
    const init = {
      method: request.method,
      headers: forwardHeaders(request.headers),
      redirect: "follow",
    };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = request.body;
    }
    const upstream = await fetch(dest.toString(), init);
    const out = new Headers(upstream.headers);
    out.set("x-fmws-vanity", "florida-man-bioscience");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: out,
    });
  },
};
