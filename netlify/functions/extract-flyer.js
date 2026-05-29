// Netlify Function: extract-flyer
// Receives a flyer image (base64) from the page, asks Claude to read the camp
// details, and returns clean JSON. The Anthropic API key stays server-side
// (set as a Netlify environment variable named ANTHROPIC_API_KEY) and is NEVER
// exposed in the public page source.

exports.handler = async (event) => {
  // CORS + method guard
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

  const { image_base64, media_type } = body;
  if (!image_base64)
    return { statusCode: 400, headers, body: JSON.stringify({ error: "No image provided" }) };

  const today = new Date().toISOString().slice(0, 10);
  const prompt =
    `Today is ${today}. This is a flyer/poster for a meditation camp, retreat, ` +
    `workshop or gathering (often Osho-related). Read it and extract the details. ` +
    `Reply with ONLY a JSON object (no prose, no markdown) with these keys: ` +
    `{"is_event": true/false, "title": "", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", ` +
    `"venue": "", "city": "", "state": "", "country": "", "phone": "", "organizer": "", ` +
    `"type": "Camp|Retreat|Workshop|Gathering|Festival", "description": ""}. ` +
    `Set is_event to false if this is not a datable event or has no clear date. ` +
    `Infer the year as ${today.slice(0,4)} or later if not stated. Keep description under 18 words. ` +
    `Leave a field as an empty string if not shown on the flyer.`;

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
        max_tokens: 600,
        messages: [{
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type: media_type || "image/jpeg", data: image_base64 } },
            { type: "text", text: prompt },
          ],
        }],
      }),
    });

    const data = await resp.json();
    if (!resp.ok)
      return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction service error" }) };

    let text = (data.content || []).map((b) => b.text || "").join("").trim();
    text = text.replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();

    let parsed;
    try { parsed = JSON.parse(text); }
    catch { return { statusCode: 200, headers, body: JSON.stringify({ is_event: false }) }; }
    if (Array.isArray(parsed)) parsed = parsed[0] || { is_event: false };
    if (typeof parsed !== "object" || parsed === null) parsed = { is_event: false };

    return { statusCode: 200, headers, body: JSON.stringify(parsed) };
  } catch (e) {
    return { statusCode: 502, headers, body: JSON.stringify({ error: "Extraction failed" }) };
  }
};
