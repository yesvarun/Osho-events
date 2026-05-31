// Netlify Function: delete-camp
// Removes a camp from submitted.json AND adds it to deleted.json (a blocklist the
// scraper respects). Password-protected. Uses GITHUB_TOKEN + DELETE_PASSWORD from
// Netlify env vars. Returns the REAL error on failure so problems are visible.

const REPO   = "yesvarun/Osho-events";
const BRANCH = "main";

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
  const PASS  = process.env.DELETE_PASSWORD;
  if (!TOKEN) return { statusCode: 500, headers, body: JSON.stringify({ error: "GITHUB_TOKEN not set in Netlify" }) };
  if (!PASS)  return { statusCode: 500, headers, body: JSON.stringify({ error: "DELETE_PASSWORD not set in Netlify" }) };

  let body;
  try { body = JSON.parse(event.body || "{}"); }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: "Bad request body" }) }; }

  if (body.password !== PASS)
    return { statusCode: 403, headers, body: JSON.stringify({ error: "Wrong password" }) };

  const id = (body.id || "").toString();
  const blockKey = (body.block_key || "").toString().toLowerCase();
  if (!id && !blockKey)
    return { statusCode: 400, headers, body: JSON.stringify({ error: "Nothing to delete (no id/key)" }) };

  const ghHeaders = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "User-Agent": "oshocamps-delete-camp",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const contentUrl = (path) => "https://api.github.com/repos/" + REPO + "/contents/" + path;

  async function readJson(path) {
    const r = await fetch(contentUrl(path) + "?ref=" + BRANCH, { headers: ghHeaders });
    if (r.status === 404) return { list: [], sha: null, exists: false };
    if (!r.ok) {
      const t = await r.text();
      throw new Error("READ " + path + " -> " + r.status + ": " + t.slice(0, 150));
    }
    const cur = await r.json();
    let list = [];
    try { list = JSON.parse(Buffer.from(cur.content, "base64").toString("utf8")); } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
    return { list: list, sha: cur.sha, exists: true };
  }

  async function writeJson(path, list, sha, msg) {
    const payload = {
      message: msg,
      content: Buffer.from(JSON.stringify(list, null, 2)).toString("base64"),
      branch: BRANCH,
    };
    if (sha) payload.sha = sha;
    const r = await fetch(contentUrl(path), {
      method: "PUT",
      headers: Object.assign({}, ghHeaders, { "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const t = await r.text();
      throw new Error("WRITE " + path + " -> " + r.status + ": " + t.slice(0, 150));
    }
    return true;
  }

  try {
    let didSomething = false;

    // 1. Remove from submitted.json if present
    const sub = await readJson("submitted.json");
    if (sub.exists) {
      const kept = sub.list.filter(function (c) { return String(c.id) !== id; });
      if (kept.length !== sub.list.length) {
        await writeJson("submitted.json", kept, sub.sha, "Delete camp " + id);
        didSomething = true;
      }
    }

    // 2. Add to deleted.json blocklist (create if missing)
    const blk = await readJson("deleted.json");
    const set = new Set(blk.list.map(function (x) { return String(x).toLowerCase(); }));
    let changed = false;
    if (id && !set.has(id.toLowerCase())) { set.add(id.toLowerCase()); changed = true; }
    if (blockKey && !set.has(blockKey)) { set.add(blockKey); changed = true; }
    if (changed) {
      const arr = Array.from(set).slice(-2000);
      await writeJson("deleted.json", arr, blk.sha, "Block deleted camp");
      didSomething = true;
    }

    return { statusCode: 200, headers, body: JSON.stringify({ ok: true, didSomething: didSomething }) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: String(e.message || e) }) };
  }
};
