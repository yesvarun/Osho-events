// netlify/functions/share
// Tiny share links:  oshocamps.com/c/<code>
// <code> is a short hash of the camp's identity (title|start_date|city). The
// function fetches the public events.json + submitted.json (free, CDN-cached),
// finds the matching camp, and renders a rich preview (flyer photo + title) plus
// a button back to oshocamps.com. No database, no per-share write.
//
// Fallback: also supports the older self-contained form  /c?t=..&s=..&i=..

const REPO_RAW = "https://raw.githubusercontent.com/yesvarun/Osho-events/main";
const SITE = "https://oshocamps.com";
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function esc(s){
  return String(s==null?"":s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
// MUST match the client's shortHash exactly.
function shortHash(s){
  s=String(s); let a=5381, b=0;
  for(let i=0;i<s.length;i++){ const c=s.charCodeAt(i); a=((a*33)^c)>>>0; b=(b*65599+c)>>>0; }
  return (a.toString(36)+b.toString(36)).slice(0,10);
}
function campKeyOf(e){
  return ((e.title||"")+"|"+(e.start_date||"")+"|"+(e.city||"")).toLowerCase();
}
function parseISO(s){ const m=/(\d{4})-(\d{2})-(\d{2})/.exec(s||""); return m? new Date(+m[1],+m[2]-1,+m[3]) : null; }
function fmtDates(sd, ed){
  const a=parseISO(sd), b=parseISO(ed);
  if(!a) return "";
  const f=d=>d.getDate()+" "+MONTHS[d.getMonth()]+" "+d.getFullYear();
  if(!b || +a===+b) return f(a);
  if(a.getMonth()===b.getMonth() && a.getFullYear()===b.getFullYear()) return a.getDate()+"–"+b.getDate()+" "+MONTHS[b.getMonth()]+" "+b.getFullYear();
  return f(a)+" – "+f(b);
}

async function loadAll(){
  const out=[];
  async function grab(name){
    try{
      const r=await fetch(REPO_RAW+"/"+name+"?t="+Math.floor(Date.now()/300000), {});
      if(!r.ok) return;
      const j=await r.json();
      const arr=Array.isArray(j)? j : (j && Array.isArray(j.events)? j.events : []);
      for(const e of arr) out.push(e);
    }catch(_){}
  }
  await Promise.all([grab("events.json"), grab("submitted.json")]);
  return out;
}

function page({title, sub, img}){
  if(!/^https?:\/\//i.test(img||"")) img = SITE+"/card-default.jpg";
  const backUrl = SITE+"/?q="+encodeURIComponent(title||"");
  const descr = (sub? sub+" · ":"")+"Tap to read the full camp details on oshocamps.com 🌸";
  return `<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(title||"Osho Camp")} · Sannyas Gatherings</title>
<meta property="og:type" content="website">
<meta property="og:site_name" content="oshocamps.com — Sannyas Gatherings">
<meta property="og:title" content="${esc(title||"Osho Meditation Camp")}">
<meta property="og:description" content="${esc(descr)}">
<meta property="og:image" content="${esc(img)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title||"Osho Meditation Camp")}">
<meta name="twitter:description" content="${esc(descr)}">
<meta name="twitter:image" content="${esc(img)}">
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Sans:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fdf6ec;color:#1a0f0f;font-family:'DM Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#fff;border:1px solid #e8d5b5;border-radius:18px;max-width:460px;width:100%;overflow:hidden;box-shadow:0 10px 40px -12px rgba(122,31,31,.25)}
.card .photo{width:100%;aspect-ratio:1/1;object-fit:cover;display:block;background:#f0e6d3}
.card .body{padding:20px}
.brand{color:#c9a84c;font-size:.72rem;letter-spacing:2px;text-transform:uppercase;font-weight:500;margin-bottom:8px}
.card h1{font-family:'Playfair Display',serif;font-size:1.4rem;line-height:1.25;color:#7a1f1f;margin-bottom:10px}
.card .sub{color:#5b5b61;font-size:.95rem;line-height:1.5;margin-bottom:18px}
.cta{display:block;text-align:center;background:linear-gradient(90deg,#7a1f1f,#a83232);color:#fdf6ec;text-decoration:none;font-family:'Playfair Display',serif;font-size:1.05rem;padding:15px;border-radius:12px;box-shadow:0 8px 22px -8px rgba(122,31,31,.5)}
.hint{text-align:center;color:#9a8b6a;font-size:.8rem;margin-top:12px;line-height:1.5}
.more{display:block;text-align:center;color:#7a1f1f;font-size:.85rem;margin-top:14px;text-decoration:none}
</style></head><body>
<div class="card">
<img class="photo" src="${esc(img)}" alt="" onerror="this.src='${SITE}/card-default.jpg'">
<div class="body">
<div class="brand">🪷 Sannyas Gatherings</div>
<h1>${esc(title||"Osho Meditation Camp")}</h1>
${sub? `<div class="sub">${esc(sub)}</div>`:""}
<a class="cta" href="${esc(backUrl)}">📖 Read full details on oshocamps.com →</a>
<div class="hint">Tap above to open the full camp — dates, venue, contact, map &amp; more — and discover other gatherings on oshocamps.com 🌸</div>
<a class="more" href="${SITE}">Browse all camps →</a>
</div></div></body></html>`;
}

exports.handler = async (event) => {
  const q = event.queryStringParameters || {};
  let title="", sub="", img="";

  // 1) tiny path form: /c/<code>  (Netlify passes it as ?id=<code>; also parse path/rawUrl as backup)
  let code = q.id || "";
  if(!code){
    const m = /\/c\/([^/?#]+)/.exec(event.path || event.rawUrl || "");
    if(m) code = decodeURIComponent(m[1]);
  }

  if(code){
    try{
      const all = await loadAll();
      const hit = all.find(e => shortHash(campKeyOf(e)) === code);
      if(hit){
        title = hit.title || "Osho Meditation Camp";
        img   = hit.flyer_url || "";
        sub   = [fmtDates(hit.start_date, hit.end_date), hit.venue, hit.city, hit.organizer].filter(Boolean).join(" · ");
      }
    }catch(_){}
  }

  // 2) self-contained fallback: /c?t=&s=&i=
  if(!title){
    title = (q.t || "Osho Meditation Camp").slice(0,160);
    sub   = (q.s || "").slice(0,240);
    img   = (q.i || "").slice(0,600);
  }

  return {
    statusCode: 200,
    headers: { "Content-Type":"text/html; charset=utf-8", "Cache-Control":"public, max-age=600" },
    body: page({title, sub, img}),
  };
};
