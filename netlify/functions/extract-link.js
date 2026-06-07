// netlify/functions/extract-link.js
//
// INSTANT link -> card. Given { url }, this fetches the page server-side, pulls the
// post's text + image (Open Graph / Twitter meta + JSON-LD), then asks Claude to turn
// it into ONE event JSON object (same shape the app expects from extract-flyer/text).
//
// Requires env var: ANTHROPIC_API_KEY
//
// NOTE: public websites, blogs and many Facebook pages expose readable meta tags and
// often a JSON-LD "Event" block (best case — exact dates). Instagram usually shows only
// a generic caption to non-logged-in fetchers, so IG links may return little; in that
// case the app tells the user to paste the caption text or queue the link.

const MODEL = "claude-sonnet-4-20250514"; // match the model your other functions use

function reply(code, obj){
  return { statusCode: code, headers: { "content-type": "application/json" }, body: JSON.stringify(obj) };
}

function pick(re, html){ const m = html.match(re); return m ? m[1].trim() : ""; }
function decode(s){
  return (s||"")
    .replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">")
    .replace(/&quot;/g,'"').replace(/&#0?39;|&apos;/g,"'").replace(/&#x27;/gi,"'")
    .replace(/&nbsp;/g," ");
}

// Pull og:/twitter:/title/description meta from raw HTML
function readMeta(html){
  const meta = {};
  meta.ogTitle = decode(pick(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i, html)
                     || pick(/<meta[^>]+name=["']twitter:title["'][^>]+content=["']([^"']+)["']/i, html));
  meta.ogDesc  = decode(pick(/<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i, html)
                     || pick(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i, html)
                     || pick(/<meta[^>]+name=["']twitter:description["'][^>]+content=["']([^"']+)["']/i, html));
  meta.ogImage = decode(pick(/<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']/i, html)
                     || pick(/<meta[^>]+name=["']twitter:image["'][^>]+content=["']([^"']+)["']/i, html));
  meta.title   = decode(pick(/<title[^>]*>([^<]+)<\/title>/i, html));
  return meta;
}

// Try to find a schema.org Event in JSON-LD (gives exact dates when present)
function readJsonLd(html){
  const blocks = [...html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  for(const b of blocks){
    try{
      let data = JSON.parse(b[1].trim());
      const arr = Array.isArray(data) ? data : (data["@graph"] ? data["@graph"] : [data]);
      for(const node of arr){
        const t = node && node["@type"];
        const isEvent = t === "Event" || (Array.isArray(t) && t.includes("Event"));
        if(isEvent && (node.startDate || node.name)){
          return node;
        }
      }
    }catch(_){ /* ignore bad JSON-LD */ }
  }
  return null;
}

const SYSTEM = `You turn the text of a social-media / website post about an Osho or meditation CAMP into ONE JSON object.
Return ONLY this JSON (no markdown, no commentary):
{
 "is_event": true or false,
 "title": "string",
 "type": "Camp"|"Retreat"|"Workshop"|"Celebration"|"Meditation"|"Event",
 "start_date": "YYYY-MM-DD" or null,
 "end_date": "YYYY-MM-DD" or null,
 "venue": "string",
 "city": "string",
 "state": "string",
 "country": "string",
 "phone": "string",
 "organizer": "string",
 "description": "string, <=200 chars, English",
 "full_text": "the complete post text, lightly organised, English; do not summarise"
}
DATE RULES:
- Read any date format and ranges ("5-9 June 2025", "30 June - 4 July", "05/06/2025", "13–16 Aug 2026").
- The YEAR may appear separately from the day/month — search the whole text and combine.
- If no year is present, use the next upcoming occurrence of that day/month.
- Indian dates are day-first. ALWAYS output ISO YYYY-MM-DD. Single-day event: end_date = start_date.
- If the text isn't about a real datable event, set is_event=false.`;

async function callClaude(key, textBlock, imageB64, imageType){
  const content = [];
  if(imageB64){
    content.push({ type:"image", source:{ type:"base64", media_type: imageType||"image/jpeg", data: imageB64 } });
  }
  content.push({ type:"text", text: "Post content follows. Return the JSON object, paying special attention to the date and year.\n\n" + textBlock });

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method:"POST",
    headers:{ "content-type":"application/json", "x-api-key":key, "anthropic-version":"2023-06-01" },
    body: JSON.stringify({ model: MODEL, max_tokens: 1500, system: SYSTEM, messages:[{ role:"user", content }] })
  });
  const data = await resp.json();
  if(data && data.error) throw new Error(data.error.message || "model error");
  const text = (data.content||[]).filter(b=>b.type==="text").map(b=>b.text).join("\n");
  const clean = text.replace(/```json/gi,"").replace(/```/g,"").trim();
  try{ return JSON.parse(clean); }
  catch(_){ const m = clean.match(/\{[\s\S]*\}/); return m ? JSON.parse(m[0]) : { is_event:false }; }
}

async function fetchAsBase64(url){
  try{
    const r = await fetch(url, { headers:{ "user-agent":"Mozilla/5.0" } });
    const type = r.headers.get("content-type") || "image/jpeg";
    if(!/^image\//.test(type)) return null;
    const buf = Buffer.from(await r.arrayBuffer());
    if(buf.length > 4*1024*1024) return null;     // skip very large images
    return { data: buf.toString("base64"), type };
  }catch(_){ return null; }
}

exports.handler = async (event) => {
  if(event.httpMethod !== "POST") return reply(405, { is_event:false, error:"Method Not Allowed" });
  try{
    const { url } = JSON.parse(event.body || "{}");
    if(!/^https?:\/\//i.test(url||"")) return reply(400, { is_event:false, error:"bad url" });
    const key = process.env.ANTHROPIC_API_KEY;
    if(!key) return reply(500, { is_event:false, error:"missing ANTHROPIC_API_KEY" });

    // 1) fetch the page
    let html = "";
    try{
      const r = await fetch(url, { headers:{
        "user-agent":"Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36",
        "accept-language":"en-IN,en;q=0.9"
      }});
      html = await r.text();
    }catch(e){ return reply(502, { is_event:false, error:"could not fetch the link" }); }

    // 2) best case: a JSON-LD Event with real dates
    const ld = readJsonLd(html);
    const meta = readMeta(html);

    if(ld && ld.startDate){
      const loc = ld.location || {};
      const addr = (loc && loc.address) || {};
      const iso = s => (String(s||"").match(/\d{4}-\d{2}-\d{2}/)||[])[0] || null;
      const ev = {
        is_event:true,
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
        description: (ld.description || meta.ogDesc || "").slice(0,200),
        full_text: ld.description || meta.ogDesc || "",
        flyer_url: (ld.image && (ld.image.url || (Array.isArray(ld.image)?ld.image[0]:ld.image))) || meta.ogImage || ""
      };
      if(ev.start_date) return reply(200, ev);
    }

    // 3) otherwise read the post text (+image) with Claude
    const textBlock = [meta.ogTitle, meta.ogDesc, meta.title].filter(Boolean).join("\n").trim();
    if(!textBlock && !meta.ogImage){
      return reply(200, { is_event:false, error:"the post didn't expose any readable text" });
    }
    let img = null;
    if(meta.ogImage) img = await fetchAsBase64(meta.ogImage);

    const ev = await callClaude(key, textBlock || "(no caption text was available)", img && img.data, img && img.type);
    if(ev && typeof ev === "object"){ ev.flyer_url = ev.flyer_url || meta.ogImage || ""; }
    return reply(200, ev || { is_event:false });

  }catch(e){
    return reply(500, { is_event:false, error: String((e && e.message) || e) });
  }
};
