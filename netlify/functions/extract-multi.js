// Netlify Function: extract-multi
// Receives a photo of a flyer that may list MANY camps/events and returns an
// ARRAY of clean event objects. Mirrors extract-text.js (same model, headers,
// parsing). API key stays server-side (Netlify env var ANTHROPIC_API_KEY).

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

  const image_base64 = (body.image_base64 || "").toString();
  const media_type = (body.media_type || "image/jpeg").toString();
  if (!image_base64)
    return { statusCode: 400, headers, body: JSON.stringify({ error: "No image provided" }) };

  const today = new Date().toISOString().slice(0, 10);
  const prompt =
    `Today is ${today}. The image is a flyer/poster that may list ONE OR MANY meditation camps, ` +
    `retreats, workshops or gatherings (often Osho-related). Read the WHOLE image carefully, including ` +
    `small text. Extract EACH distinct event as its own object. ` +
    `Reply with ONLY a JSON object (no prose, no markdown) shaped exactly like: ` +
    `{"events":[{"title":"","start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","venue":"","city":"",` +
    `"state":"","country":"","phone":"","organizer":"","type":"Camp|Retreat|Workshop|Gathering|Festival",` +
    `"description":""}]}. ` +
    `If the flyer shows only one event, return one object in the array. If it shows several dates/programmes, ` +
    `return one object per event. If there is no clear datable event, return {"events":[]}. ` +
    `DATE RULES: read any format and ranges ("5-9 June 2025", "30 June - 4 July", "05/06/2025"); the YEAR may ` +
    `be printed away from the day/month — find it and combine; if no year, use ${today.slice(0,4)} or later so ` +
    `the date is upcoming; convert Hindi/Devanagari digits; Indian dates are day-first; always ISO YYYY-MM-DD; ` +
    `single-day event end_date = start_date. Keep each description under 18 words. Leave a field empty if not mentioned.`;

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
        max_tokens: 2000,
        messages: [{
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type, data: image_base64 } },
            { type: "text", text: prompt },
          ],
        }],
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
      try { parsed = m ? JSON.parse(m[0]) : { events: [] }; }
      catch { parsed = { events: [] }; }
    }
    // accept either {events:[...]} or a bare array
    let events = Array.isArray(parsed) ? parsed : (parsed && Array.isArray(parsed.events) ? parsed.events : []);
    events = events.filter(e => e && (e.start_date || e.title));

    // Give each split event a themed Unsplash photo (portrait), since a multi-camp
    // flyer has no single per-event image. Key stays server-side.
    await Promise.all(events.map(addUnsplash));

    return { statusCode: 200, headers, body: JSON.stringify({ events }) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction failed" }) };
  }
};

// ---- themed Unsplash image per event ----
const UNSPLASH = process.env.UNSPLASH_ACCESS_KEY;
const THEMES = ["meditation","zen stones balance","himalaya mountains","candle meditation",
  "lotus flower","forest path morning","sunrise yoga silhouette","indian temple","misty lake calm","incense smoke"];
function themeFor(ev){
  const t = ((ev.title||"")+" "+(ev.type||"")+" "+(ev.description||"")).toLowerCase();
  if(/tantra|love|heart|couple/.test(t)) return "sunset silhouette love";
  if(/silen|vipassana|no.?mind|witness/.test(t)) return "misty lake calm";
  if(/mountain|himalaya|dharamsh|manali|kasol|rishikesh/.test(t)) return "himalaya mountains";
  if(/child|kids|school|teen/.test(t)) return "nature joy children";
  if(/dance|celebrat|festival|sufi|whirl/.test(t)) return "festival lights night";
  if(/yoga/.test(t)) return "sunrise yoga silhouette";
  let h=0; const s=(ev.title||"x"); for(let i=0;i<s.length;i++) h=(h*31 + s.charCodeAt(i))>>>0;
  return THEMES[h % THEMES.length];
}
async function addUnsplash(ev){
  if(!UNSPLASH || ev.flyer_url) return;
  try{
    const q = encodeURIComponent(themeFor(ev));
    const r = await fetch("https://api.unsplash.com/search/photos?orientation=portrait&per_page=12&content_filter=high&query="+q,
      { headers: { "Authorization": "Client-ID " + UNSPLASH, "Accept-Version": "v1" } });
    const d = await r.json();
    const arr = (d && d.results) || [];
    if(!arr.length) return;
    let h=0; const s=(ev.title||ev.start_date||"x"); for(let i=0;i<s.length;i++) h=(h*31 + s.charCodeAt(i))>>>0;
    const pick = arr[h % arr.length];
    if(pick && pick.urls && pick.urls.raw) ev.flyer_url = pick.urls.raw + "&w=800&q=80&fit=crop&crop=entropy";
    else if(pick && pick.urls && pick.urls.regular) ev.flyer_url = pick.urls.regular;
  }catch(_){ /* leave flyer_url empty; the site shows its own themed fallback */ }
}
