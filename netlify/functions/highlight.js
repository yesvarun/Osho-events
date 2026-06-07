// netlify/functions/highlight.js
// Owner-only. Two jobs (both password-protected with DELETE_PASSWORD = osho2026):
//   1. Highlight / un-highlight a camp:  { camp_key, on:true|false, password }
//      → maintains highlights.json (an array of camp keys) at the repo root.
//   2. Save an image crop adjustment:    { adjust:true, camp_key, crop:{x,y,scale}, password }
//      → maintains img_adjust.json (a map of camp_key → crop) at the repo root.
//
// Required Netlify env vars:
//   GITHUB_TOKEN     — same token used by save-camp (Contents read+write)
//   DELETE_PASSWORD  — the owner password (set to osho2026)

const REPO   = "yesvarun/Osho-events";
const BRANCH = "main";

async function readJson(api, headers, fallback) {
  try {
    const r = await fetch(api + "?ref=" + BRANCH + "&t=" + Date.now(), { headers });
    if (r.status === 200) {
      const d = await r.json();
      const decoded = Buffer.from(d.content || "", "base64").toString("utf8");
      return { data: JSON.parse(decoded || "null") ?? fallback, sha: d.sha };
    }
  } catch (_) {}
  return { data: fallback, sha: undefined };
}

async function writeJson(api, headers, obj, sha, msg) {
  const body = {
    message: (msg + " [skip netlify]"),
    content: Buffer.from(JSON.stringify(obj, null, 0)).toString("base64"),
    branch: BRANCH,
  };
  if (sha) body.sha = sha;
  return fetch(api, {
    method: "PUT", headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

exports.handler = async (event) => {
  if (event.httpMethod !== "POST")
    return { statusCode: 405, body: JSON.stringify({ error: "POST only" }) };

  const token = process.env.GITHUB_TOKEN;
  const ownerPw = process.env.DELETE_PASSWORD;
  if (!token) return { statusCode: 500, body: JSON.stringify({ error: "server not configured" }) };

  let p = {};
  try { p = JSON.parse(event.body || "{}"); }
  catch (_) { return { statusCode: 400, body: JSON.stringify({ error: "bad json" }) }; }

  // Password check — owner only.
  if (!ownerPw || p.password !== ownerPw)
    return { statusCode: 403, body: JSON.stringify({ error: "wrong password" }) };

  const campKey = String(p.camp_key || "").trim().toLowerCase();
  if (!campKey) return { statusCode: 400, body: JSON.stringify({ error: "missing camp_key" }) };

  const headers = {
    "Authorization": "Bearer " + token,
    "Accept": "application/vnd.github+json",
    "User-Agent": "osho-events-highlight",
  };

  // ---- Branch 1: image crop adjustment ----
  if (p.adjust) {
    const FILE = "img_adjust.json";
    const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
    for (let i = 0; i < 3; i++) {
      const { data, sha } = await readJson(api, headers, {});
      const map = (data && typeof data === "object" && !Array.isArray(data)) ? data : {};
      map[campKey] = p.crop || {};
      const res = await writeJson(api, headers, map, sha, "adjust: " + campKey);
      if (res.ok) return { statusCode: 200, body: JSON.stringify({ ok: true }) };
      if (res.status === 409 || res.status === 422) continue;
      return { statusCode: 502, body: JSON.stringify({ error: "save failed" }) };
    }
    return { statusCode: 409, body: JSON.stringify({ error: "busy, retry" }) };
  }

  // ---- Branch 2: highlight on/off ----
  const FILE = "highlights.json";
  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const on = !!p.on;
  for (let i = 0; i < 3; i++) {
    const { data, sha } = await readJson(api, headers, []);
    let list = Array.isArray(data) ? data.map(x => String(x).toLowerCase()) : [];
    const has = list.includes(campKey);
    if (on && !has) list.push(campKey);
    if (!on && has) list = list.filter(k => k !== campKey);
    const res = await writeJson(api, headers, list, sha,
      (on ? "highlight: " : "unhighlight: ") + campKey);
    if (res.ok) return { statusCode: 200, body: JSON.stringify({ ok: true, highlighted: on }) };
    if (res.status === 409 || res.status === 422) continue;
    return { statusCode: 502, body: JSON.stringify({ error: "save failed" }) };
  }
  return { statusCode: 409, body: JSON.stringify({ error: "busy, retry" }) };
};
