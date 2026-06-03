// netlify/functions/submit-link.js
// Receives a pasted social-media link from the site and appends it to
// submitted_links.json in the GitHub repo. The scraper reads that file each
// run, scrapes each link with Apify + Claude, and builds camp cards.
//
// Requires a Netlify environment variable: GITHUB_TOKEN (a fine-grained PAT
// with "Contents: read & write" on the Osho-events repo). Your other functions
// (save-camp) likely already use the same token — reuse it.

const REPO   = "yesvarun/Osho-events";
const FILE   = "submitted_links.json";
const BRANCH = "main";

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") {
    return { statusCode: 405, body: "Method not allowed" };
  }

  let payload;
  try { payload = JSON.parse(event.body || "{}"); }
  catch { return { statusCode: 400, body: "Bad JSON" }; }

  const url = (payload.url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    return { statusCode: 400, body: "Invalid URL" };
  }

  const entry = {
    url,
    note: (payload.note || "").trim(),
    at: payload.at || new Date().toISOString(),
    status: "pending"
  };

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return { statusCode: 500, body: "Server not configured (no GITHUB_TOKEN)" };
  }

  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const headers = {
    "Authorization": `Bearer ${token}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "osho-events-submit-link"
  };

  try {
    // 1) Read the current file (if it exists) to get its SHA + contents.
    let list = [];
    let sha = undefined;
    const getRes = await fetch(`${api}?ref=${BRANCH}`, { headers });
    if (getRes.ok) {
      const data = await getRes.json();
      sha = data.sha;
      try {
        const decoded = Buffer.from(data.content, "base64").toString("utf-8");
        const parsed = JSON.parse(decoded);
        if (Array.isArray(parsed)) list = parsed;
      } catch { list = []; }
    }

    // 2) Avoid duplicates; append the new link.
    if (!list.some(x => x.url === entry.url)) list.push(entry);

    // 3) Commit the updated file back.
    const newContent = Buffer.from(JSON.stringify(list, null, 2)).toString("base64");
    const putRes = await fetch(api, {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: `submit-link: ${entry.url}`,
        content: newContent,
        branch: BRANCH,
        ...(sha ? { sha } : {})
      })
    });

    if (!putRes.ok) {
      const t = await putRes.text();
      return { statusCode: 502, body: "GitHub write failed: " + t.slice(0, 200) };
    }

    return { statusCode: 200, body: JSON.stringify({ ok: true }) };
  } catch (e) {
    return { statusCode: 500, body: "Error: " + (e.message || "unknown") };
  }
};
