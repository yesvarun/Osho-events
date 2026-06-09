// netlify/functions/set-pay.js
// Two-layer, server-checked payment setup. Passwords live ONLY in env vars.
//
// 1) Organiser submits payment        { camp_key, pay_type, pay_value, password }      (password = PAY_PASSWORD)
//        -> saved as { type, value, status:"pending" }  (NOT shown to public yet)
// 2) Owner approves / publishes        { action:"approve", camp_key, password }        (password = APPROVE_PASSWORD)
//        -> status becomes "approved"  (now the Pay button shows for everyone)
// 3) Remove                            { action:"remove",  camp_key, password }        (PAY_PASSWORD or APPROVE_PASSWORD)
//
// Required Netlify env vars:
//   GITHUB_TOKEN     — repo Contents read+write (same token as save-camp)
//   PAY_PASSWORD     — organisers use this to SUBMIT a payment (kept pending)
//   APPROVE_PASSWORD — only YOU know this; used to APPROVE/publish a payment

const REPO   = "yesvarun/Osho-events";
const BRANCH = "main";
const FILE   = "payments.json";

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
    method: "PUT",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

exports.handler = async (event) => {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
  };
  if (event.httpMethod === "OPTIONS") return { statusCode: 200, headers: cors, body: "" };
  if (event.httpMethod !== "POST")
    return { statusCode: 405, headers: cors, body: JSON.stringify({ error: "POST only" }) };

  const token     = process.env.GITHUB_TOKEN;
  const payPw     = process.env.PAY_PASSWORD;
  const approvePw = process.env.APPROVE_PASSWORD;
  if (!token || !payPw || !approvePw)
    return { statusCode: 500, headers: cors, body: JSON.stringify({ error: "server not configured" }) };

  let p = {};
  try { p = JSON.parse(event.body || "{}"); }
  catch (_) { return { statusCode: 400, headers: cors, body: JSON.stringify({ error: "bad json" }) }; }

  const action  = String(p.action || "set").toLowerCase();
  const campKey = String(p.camp_key || "").trim().toLowerCase();
  if (!campKey) return { statusCode: 400, headers: cors, body: JSON.stringify({ error: "missing camp_key" }) };

  const ghHeaders = {
    "Authorization": "Bearer " + token,
    "Accept": "application/vnd.github+json",
    "User-Agent": "osho-events-set-pay",
  };
  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;

  // ---- permission check per action ----
  if (action === "approve") {
    if (p.password !== approvePw)
      return { statusCode: 403, headers: cors, body: JSON.stringify({ error: "wrong approve password" }) };
  } else if (action === "remove") {
    if (p.password !== payPw && p.password !== approvePw)
      return { statusCode: 403, headers: cors, body: JSON.stringify({ error: "wrong password" }) };
  } else { // "set" — organiser submits (pending)
    if (p.password !== payPw)
      return { statusCode: 403, headers: cors, body: JSON.stringify({ error: "wrong password" }) };
  }

  const type  = String(p.pay_type  || "").trim().toLowerCase().slice(0, 12);
  const value = String(p.pay_value || "").trim().slice(0, 200);
  if (action === "set" && !["upi", "link", "whatsapp"].includes(type))
    return { statusCode: 400, headers: cors, body: JSON.stringify({ error: "bad type" }) };

  for (let i = 0; i < 3; i++) {
    const { data, sha } = await readJson(api, ghHeaders, {});
    const map = (data && typeof data === "object" && !Array.isArray(data)) ? data : {};

    if (action === "approve") {
      if (!map[campKey]) return { statusCode: 404, headers: cors, body: JSON.stringify({ error: "no payment to approve" }) };
      map[campKey].status = "approved";
    } else if (action === "remove") {
      delete map[campKey];
    } else { // set -> pending
      map[campKey] = { type: type || "upi", value, status: "pending", at: new Date().toISOString() };
    }

    const res = await writeJson(api, ghHeaders, map, sha, action + "-pay: " + campKey);
    if (res.ok) return { statusCode: 200, headers: cors, body: JSON.stringify({ ok: true, status: (map[campKey] && map[campKey].status) || "removed" }) };
    if (res.status === 409 || res.status === 422) continue; // race — retry
    return { statusCode: 502, headers: cors, body: JSON.stringify({ error: "save failed" }) };
  }
  return { statusCode: 409, headers: cors, body: JSON.stringify({ error: "busy, retry" }) };
};
