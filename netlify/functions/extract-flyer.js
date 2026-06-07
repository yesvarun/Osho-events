// Netlify Function: extract-flyer
// Receives a photo of a camp flyer (base64) and returns clean camp JSON.
// Mirrors extract-text.js exactly (same model, headers, parsing) — only the
// input (an image) and the prompt differ. Reads the date carefully and finds
// the year even when it is written far from the day/month.
// API key stays server-side (Netlify env var ANTHROPIC_API_KEY).

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
    `Today is ${today}. The image is a flyer/poster for a meditation camp, retreat, workshop or ` +
    `gathering (often Osho-related). READ THE WHOLE IMAGE CAREFULLY — including small text, corners, ` +
    `headers, footers and banners. ` +
    `Reply with ONLY a JSON object (no prose, no markdown) with these keys: ` +
    `{"is_event": true/false, "title": "", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", ` +
    `"venue": "", "city": "", "state": "", "country": "", "phone": "", "organizer": "", ` +
    `"type": "Camp|Retreat|Workshop|Gathering|Festival", "description": "", "full_text": ""}. ` +
    `DATE RULES (important): find the date even if small or stylised; read ranges like "5-9 June 2025", ` +
    `"30 June - 4 July", "05/06/2025", "13-16 Aug 2026". The YEAR may be printed far from the day/month — ` +
    `search the whole flyer and combine it. If no year is on the flyer, use ${today.slice(0,4)} or later so ` +
    `the date is upcoming. Convert Hindi/Devanagari digits. Indian dates are day-first. ` +
    `Always output ISO YYYY-MM-DD; for a single-day event end_date = start_date. ` +
    `Set is_event false only if it is clearly not an event flyer. ` +
    `Keep description under 18 words. "full_text" = the complete flyer text, transcribed and lightly ` +
    `organised, in English; do not summarise. Leave a field as an empty string if not mentioned.`;

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
        max_tokens: 1500,
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
      try { parsed = m ? JSON.parse(m[0]) : { is_event: false }; }
      catch { parsed = { is_event: false }; }
    }
    if (Array.isArray(parsed)) parsed = parsed[0] || { is_event: false };
    if (typeof parsed !== "object" || parsed === null) parsed = { is_event: false };

    return { statusCode: 200, headers, body: JSON.stringify(parsed) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction failed" }) };
  }
};
