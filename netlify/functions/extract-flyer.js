// netlify/functions/extract-flyer.js
//
// Reads a photo of an Osho / meditation camp flyer and returns ONE JSON event object.
// Hardened to READ THE DATE carefully (small print, corners) and to find the YEAR even
// when it is written far from the day/month. Always returns ISO YYYY-MM-DD dates.
//
// Requires env var: ANTHROPIC_API_KEY  (already set in your Netlify project).
// The client (index.html) posts: { image_base64, media_type }.

// Use the same model your other functions use. claude-sonnet-4 is vision-capable.
const MODEL = "claude-sonnet-4-20250514";

const SYSTEM = `You read a photo of an Osho / meditation CAMP or event flyer and return ONE JSON object.

READ THE WHOLE IMAGE CAREFULLY. Look at every part — small text, corners, headers, footers,
decorative banners and stamps. The date and especially the YEAR are often in small or stylised
print, or set apart from the day and month.

Return ONLY this JSON object — no markdown fences, no commentary, nothing else:
{
 "is_event": true or false,
 "title": "string",
 "type": "Camp" | "Retreat" | "Workshop" | "Celebration" | "Meditation" | "Event",
 "start_date": "YYYY-MM-DD" or null,
 "end_date": "YYYY-MM-DD" or null,
 "venue": "string",
 "city": "string",
 "state": "string",
 "country": "string",
 "phone": "string",
 "organizer": "string",
 "description": "string, at most 200 characters, in English",
 "full_text": "the COMPLETE text of the flyer, transcribed and lightly organised with line breaks, translated to English; do NOT summarise"
}

DATE RULES — these matter most:
- FIND the camp date even if it is tiny, stylised, handwritten, or tucked in a corner.
- Read ranges in any form: "5-9 June 2025", "5th to 9th June", "June 5 to 9", "30 June - 4 July",
  "05/06/2025", "13–16 Aug 2026".
- The YEAR may be printed far away from the day/month (a header, a side, a logo, a footer).
  Search the ENTIRE flyer for the year and combine it with the day/month you found.
- If the year is genuinely nowhere on the flyer, choose the NEXT upcoming occurrence of that day/month.
- Convert Hindi / Devanagari digits (०-९) to normal digits.
- Indian dates are DAY-FIRST (DD/MM/YYYY).
- ALWAYS output start_date and end_date as ISO YYYY-MM-DD. For a single-day event, end_date = start_date.
- Only set is_event=false if the image is clearly NOT a camp/event flyer.`;

function reply(code, obj){
  return { statusCode: code, headers: { "content-type": "application/json" }, body: JSON.stringify(obj) };
}

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") return reply(405, { error: "Method Not Allowed" });
  try {
    const { image_base64, media_type } = JSON.parse(event.body || "{}");
    if (!image_base64) return reply(400, { is_event:false, error: "no image" });

    const key = process.env.ANTHROPIC_API_KEY;
    if (!key) return reply(500, { is_event:false, error: "missing ANTHROPIC_API_KEY" });

    const body = {
      model: MODEL,
      max_tokens: 1800,
      system: SYSTEM,
      messages: [{
        role: "user",
        content: [
          { type: "image", source: { type: "base64", media_type: media_type || "image/jpeg", data: image_base64 } },
          { type: "text", text: "Read this flyer and return the JSON object. Pay special attention to the date and the year — they may be in small print or in a corner." }
        ]
      }]
    };

    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify(body)
    });

    const data = await resp.json();
    if (data && data.error) return reply(502, { is_event:false, error: data.error.message || "model error" });

    const text = (data.content || [])
      .filter(b => b.type === "text")
      .map(b => b.text)
      .join("\n");

    const clean = text.replace(/```json/gi, "").replace(/```/g, "").trim();
    let obj;
    try {
      obj = JSON.parse(clean);
    } catch (_) {
      const m = clean.match(/\{[\s\S]*\}/);   // last-ditch: pull the first {...} block
      obj = m ? JSON.parse(m[0]) : { is_event: false };
    }
    return reply(200, obj);

  } catch (e) {
    return reply(500, { is_event:false, error: String((e && e.message) || e) });
  }
};
