// netlify/functions/interest.js
// Increments the "interested" count for a camp and returns the new total.
// Stores all counts in interest.json at the repo root, via the GitHub Contents API.
// Anyone can call this (no password) — the front-end limits one press per device.
//
// Required Netlify env var: GITHUB_TOKEN  (a fine-grained token with Contents: read+write
// on the yesvarun/Osho-events repo — the SAME token your save-camp function already uses).

const REPO   = "yesvarun/Osho-events";
const BRANCH = "main";
const FILE   = "interest.json";

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") {
    return { statusCode: 405, body: JSON.stringify({ error: "POST only" }) };
  }
  const token = process.env.GITHUB_TOKEN;
  if (!token) return { statusCode: 500, body: JSON.stringify({ error: "server not configured" }) };

  let campKey = "";
  try { campKey = String(JSON.parse(event.body || "{}").camp_key || "").trim().toLowerCase(); }
  catch (_) { return { statusCode: 400, body: JSON.stringify({ error: "bad json" }) }; }
  if (!campKey) return { statusCode: 400, body: JSON.stringify({ error: "missing camp_key" }) };

  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const headers = {
    "Authorization": "Bearer " + token,
    "Accept": "application/vnd.github+json",
    "User-Agent": "osho-events-interest",
  };

  // Retry a couple of times in case two people press at the same instant (sha conflict).
  for (let attempt = 0; attempt < 3; attempt++) {
    let counts = {}, sha = undefined;
    try {
      const getRes = await fetch(api + "?ref=" + BRANCH + "&t=" + Date.now(), { headers });
      if (getRes.status === 200) {
        const data = await getRes.json();
        sha = data.sha;
        const decoded = Buffer.from(data.content || "", "base64").toString("utf8");
        counts = JSON.parse(decoded || "{}");
        if (typeof counts !== "object" || Array.isArray(counts)) counts = {};
      }
      // 404 → file doesn't exist yet; we'll create it.
    } catch (_) { counts = {}; }

    counts[campKey] = (Number(counts[campKey]) || 0) + 1;

    const putBody = {
      message: "interest: " + campKey + " [skip netlify]",
      content: Buffer.from(JSON.stringify(counts, null, 0)).toString("base64"),
      branch: BRANCH,
    };
    if (sha) putBody.sha = sha;

    const putRes = await fetch(api, {
      method: "PUT", headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(putBody),
    });

    if (putRes.ok) {
      return { statusCode: 200, body: JSON.stringify({ ok: true, count: counts[campKey] }) };
    }
    if (putRes.status === 409 || putRes.status === 422) continue;  // sha race → retry
    const txt = await putRes.text();
    return { statusCode: 502, body: JSON.stringify({ error: "save failed", detail: txt.slice(0,200) }) };
  }
  return { statusCode: 409, body: JSON.stringify({ error: "busy, please retry" }) };
};
