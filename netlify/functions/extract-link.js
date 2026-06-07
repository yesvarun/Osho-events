// Netlify Function: extract-link
// Receives a post URL, fetches the page server-side, reads its text + image
// (Open Graph / Twitter meta + JSON-LD), and returns clean camp JSON.
// Mirrors extract-text.js (same model, headers, parsing). The picture AND the
// post text are both sent to Claude so it can collect all the details.
// API key stays server-side (Netlify env var ANTHROPIC_API_KEY).

function pick(re, html){ const m = html.match(re); return m ? m[1].trim() : ""; }
function decode(s){
  return (s||"")
    .replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">")
    .replace(/&quot;/g,'"').replace(/&#0?39;|&apos;/g,"'").replace(/&#x27;/gi,"'").replace(/&nbsp;/g," ");
}
function readMeta(html){
  return {
    ogTitle: decode(pick(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i, html)
                 || pick(/<meta[^>]+name=["']twitter:title["'][^>]+content=["']([^"']+)["']/i, html)),
    ogDesc:  decode(pick(/<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i, html)
                 || pick(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i, html)
                 || pick(/<meta[^>]+name=["']twitter:description["'][^>]+content=["']([^"']+)["']/i, html)),
    ogImage: decode(pick(/<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']/i, html)
                 || pick(/<meta[^>]+name=["']twitter:image["'][^>]+content=["']([^"']+)["']/i, html)),
    title:   decode(pick(/<title[^>]*>([^<]+)<\/title>/i, html)),
  };
}
function readJsonLd(html){
  const blocks = [...html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  for (const b of blocks){
    try {
      let data = JSON.parse(b[1].trim());
      const arr = Array.isArray(data) ? data : (data["@graph"] ? data["@graph"] : [data]);
      for (const node of arr){
        const t = node && node["@type"];
        const isEvent = t === "Event" || (Array.isArray(t) && t.includes("Event"));
        if (isEvent && (node.startDate || node.name)) return node;
      }
    } catch (_) {}
  }
  return null;
}
async function fetchImageB64(url){
  try {
    const r = await fetch(url, { headers: { "user-agent": "Mozilla/5.0" } });
    const type = (r.headers.get("content-type") || "image/jpeg").split(";")[0];
    if (!/^image\//.test(type)) return null;
    const buf = Buffer.from(await r.arrayBuffer());
    if (buf.length > 4 * 1024 * 1024) return null;
    return { data: buf.toString("base64"), type };
  } catch (_) { return null; }
}

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

  const API_KEY = process.env.ANTHROPIC_API_KEY;
  if (!API_KEY)
    return { statusCode: 500, headers, body: JSON.stringify({ error: "Server not configured" }) };

  let body;
  try { body = JSON.parse(event.body || "{}"); }
  catch { return { statusCode: 400, headers, body: JSON.stringify({ error: "Bad request" }) }; }

  const url = (body.url || "").toString();
  if (!/^https?:\/\//i.test(url))
    return { statusCode: 400, headers, body: JSON.stringify({ error: "No valid url" }) };

  // 1) fetch the page
  let html = "";
  try {
    const r = await fetch(url, { headers: {
      "user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36",
      "accept-language": "en-IN,en;q=0.9",
    }});
    html = await r.text();
  } catch (_) {
    return { statusCode: 200, headers, body: JSON.stringify({ is_event: false, error: "could not fetch the link" }) };
  }

  const meta = readMeta(html);
  const ld = readJsonLd(html);
  const today = new Date().toISOString().slice(0, 10);

  // 2) best case: structured Event data with real dates
  if (ld && ld.startDate) {
    const loc = ld.location || {};
    const addr = (loc && loc.address) || {};
    const iso = s => (String(s||"").match(/\d{4}-\d{2}-\d{2}/)||[])[0] || "";
    const ev = {
      is_event: true,
      title: ld.name || meta.ogTitle || "Meditation Camp",
      type: "Camp",
      start_date: iso(ld.startDate),
      end_date: iso(ld.endDate) || iso(ld.startDate),
      venue: (loc && loc.name) || addr.streetAddress || "",
      city: addr.addressLocality || "",
      state: addr.addressRegion || "",
      country: addr.addressCountry || "India",
      phone: "",
      organizer: (ld.organizer && (ld.organizer.name || ld.organizer)) || "",
      description: (ld.description || meta.ogDesc || "").slice(0, 140),
      full_text: ld.description || meta.ogDesc || "",
      flyer_url: (ld.image && (ld.image.url || (Array.isArray(ld.image) ? ld.image[0] : ld.image))) || meta.ogImage || "",
    };
    if (ev.start_date) return { statusCode: 200, headers, body: JSON.stringify(ev) };
  }

  // 3) otherwise read the post text + image with Claude (same model as extract-text)
  const postText = [meta.ogTitle, meta.ogDesc, meta.title].filter(Boolean).join("\n").trim();
  if (!postText && !meta.ogImage)
    return { statusCode: 200, headers, body: JSON.stringify({ is_event: false, error: "the post didn't expose readable text" }) };

  const img = meta.ogImage ? await fetchImageB64(meta.ogImage) : null;

  const prompt =
    `Today is ${today}. Below is the text of a social-media / website post (and an image, if present) ` +
    `about a meditation camp, retreat, workshop or gathering (often Osho-related). Use BOTH the image ` +
    `and the text to collect every detail. ` +
    `Reply with ONLY a JSON object (no prose, no markdown) with these keys: ` +
    `{"is_event": true/false, "title": "", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", ` +
    `"venue": "", "city": "", "state": "", "country": "", "phone": "", "organizer": "", ` +
    `"type": "Camp|Retreat|Workshop|Gathering|Festival", "description": "", "full_text": ""}. ` +
    `Read any date format and ranges; the YEAR may be separate from the day/month — combine them; ` +
    `if no year, use ${today.slice(0,4)} or later so it is upcoming. Indian dates are day-first. ` +
    `Always output ISO YYYY-MM-DD; single-day event end_date = start_date. ` +
    `Set is_event false if there is no clear datable event. Keep description under 18 words. ` +
    `"full_text" = the full post text, lightly organised, English. Leave a field empty if not mentioned.` +
    `\n\nPOST TEXT:\n` + (postText || "(no caption text was available)");

  const content = [];
  if (img) content.push({ type: "image", source: { type: "base64", media_type: img.type, data: img.data } });
  content.push({ type: "text", text: prompt });

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1200,
        messages: [{ role: "user", content }],
      }),
    });

    const data = await resp.json();
    if (!resp.ok)
      return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction service error" }) };

    let out = (data.content || []).map((b) => b.text || "").join("").trim();
    out = out.replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();

    let parsed;
    try { parsed = JSON.parse(out); }
    catch {
      const m = out.match(/\{[\s\S]*\}/);
      try { parsed = m ? JSON.parse(m[0]) : { is_event: false }; }
      catch { parsed = { is_event: false }; }
    }
    if (Array.isArray(parsed)) parsed = parsed[0] || { is_event: false };
    if (typeof parsed !== "object" || parsed === null) parsed = { is_event: false };
    if (!parsed.flyer_url) parsed.flyer_url = meta.ogImage || "";

    return { statusCode: 200, headers, body: JSON.stringify(parsed) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction failed" }) };
  }
};
