// netlify/functions/share
// Renders a per-camp SHARE PAGE with rich link-preview meta tags (Open Graph /
// Twitter), using data passed in the URL (self-contained — no lookup needed).
// WhatsApp/Instagram/Facebook read the meta tags and show a card with the flyer
// photo + title. A human who taps it sees a full card + a button to open
// oshocamps.com for the complete details.
//
// URL: /.netlify/functions/share?t=Title&s=Dates%20%C2%B7%20Venue&i=<flyerUrl>

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

exports.handler = async (event) => {
  const q = event.queryStringParameters || {};
  const title = (q.t || "Osho Meditation Camp").slice(0, 160);
  const sub   = (q.s || "").slice(0, 240);
  let img     = (q.i || "").slice(0, 600);

  const SITE = "https://oshocamps.com";
  if (!/^https?:\/\//i.test(img)) img = SITE + "/card-default.jpg";

  // This page's own URL (for og:url)
  const selfUrl = event.rawUrl || (SITE + "/.netlify/functions/share?" + (event.rawQuery || ""));
  // Where "read full details" sends them — the site, pre-searched for this camp
  const backUrl = SITE + "/?q=" + encodeURIComponent(title);

  const descr = (sub ? sub + " · " : "") + "Tap to read the full camp details on oshocamps.com 🌸";

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(title)} · Sannyas Gatherings</title>

<!-- Rich link preview -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="oshocamps.com — Sannyas Gatherings">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(descr)}">
<meta property="og:image" content="${esc(img)}">
<meta property="og:url" content="${esc(selfUrl)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title)}">
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
</style>
</head>
<body>
  <div class="card">
    <img class="photo" src="${esc(img)}" alt="" onerror="this.src='${SITE}/card-default.jpg'">
    <div class="body">
      <div class="brand">🪷 Sannyas Gatherings</div>
      <h1>${esc(title)}</h1>
      ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}
      <a class="cta" href="${esc(backUrl)}">📖 Read full details on oshocamps.com →</a>
      <div class="hint">Tap above to open the full camp — dates, venue, contact, map &amp; more — and discover other gatherings on oshocamps.com 🌸</div>
      <a class="more" href="${SITE}">Browse all camps →</a>
    </div>
  </div>
</body>
</html>`;

  return {
    statusCode: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=600" },
    body: html,
  };
};
