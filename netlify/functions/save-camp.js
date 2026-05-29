// Netlify Function: save-camp
// Appends a user-submitted camp to submitted.json in the GitHub repo, so EVERY
// visitor sees it (the site loads submitted.json alongside events.json).
// Uses a GitHub token stored server-side as the Netlify env var GITHUB_TOKEN.
// The token is NEVER exposed in the public page.

const REPO   = "yesvarun/Osho-events";      // owner/repo
const BRANCH = "main";
const FILE   = "submitted.json";

exports.handler = async (event) => {
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
  };
  if (event.httpMethod === "OPTIONS") return { statusCode: 200, headers, body: "" };
  if (event.httpMethod !== "POST")
    return { statusCode: 405, headers, body: JSON.stringify({ error: "Method not allowed" }) };

  const TOKEN = process.env.GITHUB_TOKEN;
  if (!TOKEN)
    return { statusCode: 500, headers, body: JSON.stringify({ error: "Server not configured" }) };

  let camp;
  try { camp = JSON.parse(event.body || "{}").camp; }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: "Bad request" }) }; }
  if (!camp || !camp.title || !camp.start_date)
    return { statusCode: 400, headers, body: JSON.stringify({ error: "Missing camp data" }) };

  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const ghHeaders = {
    "Authorization": `Bearer ${TOKEN}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "oshocamps-save-camp",
  };

  try {
    // 1. Read the current submitted.json (may not exist yet)
    let list = [], sha = undefined;
    const getResp = await fetch(`${api}?ref=${BRANCH}`, { headers: ghHeaders });
    if (getResp.status === 200) {
      const cur = await getResp.json();
      sha = cur.sha;
      try {
        const decoded = Buffer.from(cur.content, "base64").toString("utf8");
        list = JSON.parse(decoded);
        if (!Array.isArray(list)) list = [];
      } catch { list = []; }
    }

    // 2. Clean the incoming camp to a safe, fixed shape (ignore anything extra)
    const clean = {
      id: "sub_" + Date.now(),
      title: String(camp.title || "").slice(0, 140),
      type: String(camp.type || "Camp").slice(0, 30),
      start_date: String(camp.start_date || "").slice(0, 10),
      end_date: String(camp.end_date || camp.start_date || "").slice(0, 10),
      venue: String(camp.venue || "").slice(0, 140),
      city: String(camp.city || "").slice(0, 80),
      state: String(camp.state || "").slice(0, 80),
      country: String(camp.country || "India").slice(0, 60),
      phone: String(camp.phone || "").slice(0, 40),
      flyer_url: String(camp.flyer_url || "").slice(0, 400),
      organizer: String(camp.organizer || "").slice(0, 140),
      description: String(camp.description || "").slice(0, 300),
      source_platform: "Community upload",
      submitted_at: new Date().toISOString(),
    };

    // 3. De-dupe (same title + start + city) and cap the list size
    const key = (c) => (c.title + "|" + c.start_date + "|" + c.city).toLowerCase();
    if (!list.some((c) => key(c) === key(clean))) list.unshift(clean);
    // Drop anything already ended, and keep at most 500 entries
    const today = new Date().toISOString().slice(0, 10);
    list = list.filter((c) => (c.end_date || c.start_date) >= today).slice(0, 500);

    // 4. Write back
    const put = await fetch(api, {
      method: "PUT",
      headers: { ...ghHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "Community upload: " + clean.title,
        content: Buffer.from(JSON.stringify(list, null, 2)).toString("base64"),
        branch: BRANCH,
        ...(sha ? { sha } : {}),
      }),
    });
    if (!put.ok) {
      const err = await put.text();
      return { statusCode: 502, headers, body: JSON.stringify({ error: "Save failed", detail: err.slice(0,200) }) };
    }
    return { statusCode: 200, headers, body: JSON.stringify({ ok: true, camp: clean }) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Save failed" }) };
  }
};
