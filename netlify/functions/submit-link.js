// Netlify Function: submit-link
// Appends a user-submitted social-media post LINK to submitted_links.json in the
// GitHub repo. The scraper reads that file each run, scrapes each link with Apify
// + Claude, and builds a camp card. Same token/style as save-camp.
// Uses the GitHub token stored server-side as the Netlify env var GITHUB_TOKEN.

const REPO   = "yesvarun/Osho-events";      // owner/repo
const BRANCH = "main";
const FILE   = "submitted_links.json";

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

  let body;
  try { body = JSON.parse(event.body || "{}"); }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: "Bad request" }) }; }

  const url = String(body.url || "").trim();
  if (!/^https?:\/\//i.test(url))
    return { statusCode: 400, headers, body: JSON.stringify({ error: "Invalid URL" }) };

  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const ghHeaders = {
    "Authorization": `Bearer ${TOKEN}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "oshocamps-submit-link",
  };

  try {
    // 1. Read the current submitted_links.json (may not exist yet)
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

    // 2. Clean the incoming link to a safe fixed shape
    const clean = {
      url: url.slice(0, 400),
      note: String(body.note || "").slice(0, 200),
      at: new Date().toISOString(),
      status: "pending",
    };

    // 3. De-dupe by URL; newest first; cap at 300 entries
    if (!list.some((x) => x && x.url === clean.url)) list.unshift(clean);
    list = list.slice(0, 300);

    // 4. Write back
    const put = await fetch(api, {
      method: "PUT",
      headers: { ...ghHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "Submitted link: " + clean.url.slice(0, 80) + " [skip netlify]",
        content: Buffer.from(JSON.stringify(list, null, 2)).toString("base64"),
        branch: BRANCH,
        ...(sha ? { sha } : {}),
      }),
    });
    if (!put.ok) {
      const err = await put.text();
      return { statusCode: 502, headers, body: JSON.stringify({ error: "Save failed", detail: err.slice(0, 200) }) };
    }
    return { statusCode: 200, headers, body: JSON.stringify({ ok: true }) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Save failed" }) };
  }
};
