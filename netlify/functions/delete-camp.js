// Netlify Function: delete-camp
// Removes a camp from submitted.json by its id. Password-protected: the caller must
// send the correct owner password (checked against env var DELETE_PASSWORD).
// Uses GITHUB_TOKEN (server-side) to write the repo. Neither secret is in the page.

const REPO   = "yesvarun/Osho-events";
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
  const PASS  = process.env.DELETE_PASSWORD;
  if (!TOKEN || !PASS)
    return { statusCode: 500, headers, body: JSON.stringify({ error: "Server not configured" }) };

  let body;
  try { body = JSON.parse(event.body || "{}"); }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: "Bad request" }) }; }

  // Check the password
  if (body.password !== PASS)
    return { statusCode: 403, headers, body: JSON.stringify({ error: "Wrong password" }) };

  const id = (body.id || "").toString();
  const blockKey = (body.block_key || "").toString();   // title|start|city for scraper-fed camps
  if (!id && !blockKey)
    return { statusCode: 400, headers, body: JSON.stringify({ error: "No id" }) };

  const api = `https://api.github.com/repos/${REPO}/contents/${FILE}`;
  const ghHeaders = {
    "Authorization": `Bearer ${TOKEN}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "oshocamps-delete-camp",
  };

  // Helper to read+write a JSON array file in the repo
  async function readJsonFile(path){
    const u = `https://api.github.com/repos/${REPO}/contents/${path}`;
    const r = await fetch(`${u}?ref=${BRANCH}`, { headers: ghHeaders });
    if (r.status !== 200) return { list: [], sha: undefined };
    const cur = await r.json();
    let list = [];
    try { list = JSON.parse(Buffer.from(cur.content, "base64").toString("utf8")); } catch { list = []; }
    if (!Array.isArray(list)) list = [];
    return { list, sha: cur.sha };
  }
  async function writeJsonFile(path, list, sha, msg){
    const u = `https://api.github.com/repos/${REPO}/contents/${path}`;
    return fetch(u, {
      method: "PUT",
      headers: { ...ghHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: msg,
        content: Buffer.from(JSON.stringify(list, null, 2)).toString("base64"),
        branch: BRANCH,
        ...(sha ? { sha } : {}),
      }),
    });
  }

  try {
    // 1. Remove from submitted.json (community/your uploads)
    const sub = await readJsonFile(FILE);
    const before = sub.list.length;
    const newSub = sub.list.filter((c) => c.id !== id);
    if (newSub.length !== before) {
      await writeJsonFile(FILE, newSub, sub.sha, "Delete camp " + id);
    }

    // 2. Add to deleted.json blocklist so the SCRAPER skips it forever.
    //    We store both the id and a title|start|city key (scraper-fed camps get new ids
    //    each run, so the key is what makes the block stick across scrapes).
    const blk = await readJsonFile("deleted.json");
    let changed = false;
    if (id && !blk.list.includes(id)) { blk.list.push(id); changed = true; }
    if (blockKey && !blk.list.includes(blockKey)) { blk.list.push(blockKey); changed = true; }
    if (changed) {
      // cap the blocklist so it can't grow forever
      const capped = blk.list.slice(-2000);
      await writeJsonFile("deleted.json", capped, blk.sha, "Block deleted camp");
    }

    return { statusCode: 200, headers, body: JSON.stringify({ ok: true }) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Delete failed" }) };
  }
};
