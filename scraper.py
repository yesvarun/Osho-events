#!/usr/bin/env python3
"""
Sannyas Gatherings — hourly scraper pipeline
=============================================
Flow:
  1. Apify scrapes Instagram + Facebook for Osho event posts (hashtags + search).
  2. Claude (Haiku) reads each post and extracts structured event fields.
  3. We dedupe, map country -> region, drop past events, write events.json.

Run locally:   APIFY_TOKEN=... ANTHROPIC_API_KEY=... python scraper.py
Run every 6h:  see refresh.yml (GitHub Actions cron)

Output: events.json  (same shape the HTML app expects)
"""

import os, json, time, hashlib, datetime as dt
import re as _re
import requests

# Realistic browser headers — some sites (e.g. Osho World) return 403 to obvious bots.
# Looking like a normal Chrome browser avoids that blocking.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
APIFY_TOKEN     = os.environ["APIFY_TOKEN"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
UNSPLASH_KEY    = os.environ.get("UNSPLASH_ACCESS_KEY", "")   # optional: themed images for camps with no real photo
EXTRACT_MODEL   = "claude-haiku-4-5-20251001"   # cheap + fast for per-post extraction
OUTPUT_FILE     = "events.json"

# Apify actors (creator~actor-name). Swap if you prefer a different actor.
IG_ACTOR = "apify~instagram-scraper"
FB_PAGES_ACTOR  = "apify~facebook-posts-scraper"            # scrapes specific page URLs (reliable)
FB_GROUPS_ACTOR = "apify~facebook-groups-scraper"           # scrapes public group posts
# Facebook SEARCH actor — works cheaply (40 results ≈ $0.10). Set the exact actor ID here.
# Find it on the actor's page header (looks like "creator~facebook-search-...").
FB_SEARCH_ACTOR = "danek~facebook-search-ppr"
FB_SEARCH_ENABLED = True          # ON: searches Facebook by keyword (like IG hashtag search)

# JS-rendered websites: pages whose events only appear after JavaScript runs.
# DISABLED for now — the apify~web-scraper input config needs more work.
# When re-enabled, uncomment the URLs below and (if needed) switch the actor.
JS_RENDER_URLS = set()   # empty = skip JS render path, use HTTP only
# JS_RENDER_URLS = {
#     "https://wellness.oshohimalayas.com/all_upcoming_meditation_courses?gad_source=1",
#     "https://www.humaniversity.com/courses/",
# }
JS_RENDER_ACTOR = "apify~web-scraper"

# Known, active Osho Facebook PAGES — adds a reliable source beyond search.
FB_PAGES = [
    "https://www.facebook.com/osho.international.meditation.resort/",
    "https://www.facebook.com/OSHOInternational/",
    "https://www.facebook.com/oshoworld/",
    "https://www.facebook.com/oshohimalayas/",        # Osho Himalayas (144k+ followers, very active)
    "https://www.facebook.com/osnisarga/",            # Osho Nisarga
    "https://www.facebook.com/oshotapoban1/",         # Osho Tapoban, Nepal
    "https://www.facebook.com/zorbathebuddhaindia/",  # Zorba the Buddha India
    "https://www.facebook.com/oshosadhanapath/",      # Osho Sadhana Path, Nargol
]

# Public Osho Facebook GROUPS to scrape. Groups are where many regional camps get
# announced. Paste public group URLs here, e.g. "https://www.facebook.com/groups/123456/".
# NOTE: PRIVATE groups need your own login cookies — public groups work without login.
FB_GROUPS = [
    # "https://www.facebook.com/groups/oshomeditation/",
    # "https://www.facebook.com/groups/oshosannyas/",
]

# What we look for. Trimmed to the most productive tags to save credit.
IG_HASHTAGS = ["oshomeditation", "oshocamp", "oshoretreat", "oshointernational",
               # Hindi / Punjabi / Nepali / regional tags to catch regional-language posts:
               "ओशो", "ओशोध्यान", "ध्यानशिविर", "ओशोशिविर", "साधनाशिविर",
               "ਓਸ਼ੋ", "ਧਿਆਨ",
               # Nepal-focused:
               "oshotapoban", "oshonepal", "meditationnepal", "ओशोनेपाल", "ध्यानशिविरनेपाल"]
# Specific Instagram PROFILES to scrape (more reliable than hashtags).
# Paste profile URLs, e.g. "https://www.instagram.com/oshointernational/".
IG_PROFILES = [
    "https://www.instagram.com/oshointernational/",
    "https://www.instagram.com/tapobaninternational/",   # Osho Tapoban, Nepal — correct handle
    "https://www.instagram.com/oshobliss_experiences/",  # Osho Bliss, Rishikesh
    "https://www.instagram.com/zorbathebuddhaindia/",    # Zorba the Buddha, India
    "https://www.instagram.com/osho_humaniversity/",     # Osho Humaniversity, Netherlands
    "https://www.instagram.com/oshohimalayas/",          # Osho Himalayas (website is JS-rendered)
    # add more Osho centre / organiser accounts here
]
# Facebook SEARCH terms — searched ONE AT A TIME, so each adds a small cost.
# Covers common camp types. Trim if cost matters; add if you want wider reach.
SEARCH_TERMS = ["osho meditation camp", "osho retreat", "osho meditation shivir",
                "mystic rose meditation", "osho festival", "ध्यान शिविर", "osho tapoban"]

# IMPORTANT: Instagram now blocks most ANONYMOUS hashtag browsing, which is the #1 reason
# a hashtag scrape returns 0 posts. If your Apify test confirms this, paste a logged-in
# Instagram session cookie below (or set it as a GitHub secret IG_SESSION_COOKIE).
# How to get it: log into instagram.com in a browser → DevTools → Application →
# Cookies → copy the value of the "sessionid" cookie. Format: "sessionid=XXXX…"
IG_SESSION_COOKIE = os.environ.get("IG_SESSION_COOKIE", "")

POSTS_PER_QUERY = 40          # more posts per run = more camps found (and higher cost per run)
MAX_POST_AGE_DAYS = 45        # balanced window — wide enough to catch advance announcements, modest cost

# VISION: when a post has little/no caption text, read its flyer IMAGE with Claude vision.
# Costs more per image, so it only fires for caption-less posts (cost-aware). Set False to disable.
VISION_FOR_IMAGE_POSTS = True
VISION_MIN_CAPTION_LEN = 40   # if caption is shorter than this, try reading the image instead

# LOCAL FLYERS: drop WhatsApp camp flyer images into this folder in your repo, and the
# scraper reads them with Claude vision into events (no Apify cost — they're your own files).
FLYERS_DIR = "flyers"
# Public base URL for files in your repo, so uploaded flyers can DISPLAY on their cards.
# This is your repo's raw URL (same host that serves events.json).
FLYERS_BASE_URL = "https://raw.githubusercontent.com/yesvarun/Osho-events/refs/heads/main/flyers/"

# Re-hosted card images: IG/FB image URLs get blocked when embedded on another site, so we
# DOWNLOAD them into this repo folder and serve from our own URL (never blocked, never expires).
CARD_IMG_DIR = "card_images"
CARD_IMG_BASE_URL = "https://raw.githubusercontent.com/yesvarun/Osho-events/refs/heads/main/card_images/"

# WORDPRESS "The Events Calendar" sites — these expose a clean JSON API of their events.
# FREE to read (no Apify, no AI). Add any Osho centre that runs this plugin.
# Each entry: (base_site_url, default_country). The scraper hits {site}/wp-json/tribe/events/v1/events
WP_EVENT_SITES = [
    ("https://tapoban.com", "Nepal"),   # Osho Tapoban, Kathmandu — major Nepal centre
]

# iCAL FEEDS — the universal way to read centre calendars (no Apify, no AI cost).
# Many Osho centres expose a .ics feed. Each entry: (ics_url, default_country, organizer_name).
# To find a centre's feed: look for "Add to Calendar" / "Save iCal" / "Subscribe" on their events page.
# Common patterns: {site}/events/?ical=1  or  {site}/?post_type=tribe_events&ical=1
ICAL_FEEDS = [
    ("https://tapoban.com/events/?ical=1", "Nepal", "Osho Tapoban"),
    ("https://oshoworld.com/events/?ical=1", "India", "Osho World"),
    ("https://www.oshonisarga.com/upcoming-programs/calendar/?ical=1", "India", "Osho Nisarga"),
    ("https://www.oshosandiego.com/?post_type=tribe_events&ical=1", "USA", "Osho San Diego"),
    # Add more centres' .ics feeds here as you find them.
]

# HTML EVENT PAGES — for centres WITHOUT an iCal/JSON feed (e.g. custom-built sites).
# The scraper fetches the page and Claude reads the listed camps from it. Free of Apify
# (uses Claude, which you already pay for).
# Each entry: (events_page_url, default_country, organizer, contact_phone, venue_address).
# The phone/venue are applied to every camp from that page so cards have a way to enquire.
HTML_EVENT_PAGES = [
    ("https://oshoworld.com/events", "India", "Osho Dham / Osho World",
     "011-25319026",
     "Osho Dham, 44 Jhatikra Road, Pandwala Khurd, Near Najafgarh, New Delhi 110043"),
    ("https://www.oshohimalayas.com/courses/", "India", "Osho Himalayas",
     "+91-7071042042",
     "Osho Himalayas, Dharamshala valley, Himachal Pradesh (45 min from Dharamshala airport)"),
    ("https://www.oshonisarga.com/upcoming-programs/calendar", "India", "Osho Nisarga",
     "+91-9418037370",
     "Osho Nisarga, Dharamshala, Himachal Pradesh"),
    ("https://www.oshoresortnargol.com/", "India", "Osho Sadhana Path (Nargol)",
     "+91-7509076090",
     "Osho Sadhana Path International Meditation Resort, Nargol Beach, Gujarat"),
    ("https://oshoramana.com/upcoming-events", "India", "Osho Ramana",
     "",
     "Osho Ramana, Tiruvannamalai, Tamil Nadu"),
    ("https://www.oshorisk.com/events/", "Denmark", "Osho Risk",
     "+45 75752500",
     "Osho Risk Meditation Center, Lalit, Braedstrup, Denmark"),
    ("https://www.oshouta.de/de/programm", "Germany", "Osho Uta",
     "+49 221 9520320",
     "Osho UTA Institut, Venloer Str. 5-7, 50672 Köln, Germany"),
    ("https://www.humaniversity.com/courses/", "Netherlands", "Osho Humaniversity",
     "+31 72 506 4114",
     "OSHO Humaniversity, Dr. Wiardi Beckmanlaan 8, Egmond aan Zee, Netherlands"),
    ("https://www.osho.com/osho-multiversity/programs/courses", "India", "OSHO Multiversity (Pune)",
     "+91 20 6601 9999",
     "OSHO International Meditation Resort, 17 Koregaon Park, Pune, Maharashtra"),
]

# Country -> region grouping (must match the app's REGION_MAP)
REGION_MAP = {
    "India":"India",
    "Nepal":"Asia","Sri Lanka":"Asia","Thailand":"Asia","Japan":"Asia","China":"Asia",
    "Indonesia":"Asia","Singapore":"Asia","Malaysia":"Asia","Vietnam":"Asia","South Korea":"Asia",
    "Israel":"Asia","Iran":"Asia","UAE":"Asia","Taiwan":"Asia","Philippines":"Asia",
    "Germany":"Europe","Italy":"Europe","Spain":"Europe","France":"Europe","UK":"Europe",
    "United Kingdom":"Europe","Netherlands":"Europe","Switzerland":"Europe","Greece":"Europe",
    "Portugal":"Europe","Sweden":"Europe","Austria":"Europe","Denmark":"Europe","Ireland":"Europe",
    "Poland":"Europe","Czech Republic":"Europe","Hungary":"Europe","Croatia":"Europe",
    "USA":"Americas","United States":"Americas","Canada":"Americas","Mexico":"Americas",
    "Brazil":"Americas","Argentina":"Americas","Chile":"Americas","Colombia":"Americas",
    "Peru":"Americas","Costa Rica":"Americas",
    "Australia":"Pacific","New Zealand":"Pacific","Fiji":"Pacific",
    "Russia":"Russia",
}

APIFY_BASE = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token=" + APIFY_TOKEN


# ----------------------------------------------------------------------
# 1. SCRAPE  (Apify)
# ----------------------------------------------------------------------
def run_actor(actor, payload):
    """Run an Apify actor synchronously and return its dataset items. Loud on failure."""
    url = APIFY_BASE.format(actor=actor)
    print(f"  → calling actor: {actor}")
    try:
        r = requests.post(url, json=payload, timeout=310)
        print(f"    HTTP {r.status_code}")
        if r.status_code == 408:
            print("    !! 408 timeout — actor took >300s. Lower POSTS_PER_QUERY or fewer hashtags.")
            return []
        if r.status_code not in (200, 201):
            # show Apify's error message so we know WHY (bad token, wrong actor, bad input…)
            print(f"    !! Apify error body: {r.text[:500]}")
            return []
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        print(f"    ✓ received {len(items)} items")
        if items:
            first = items[0]
            print(f"    sample keys: {list(first.keys())[:20]}")
            # show the value of whichever field looks like the caption
            for k in ("caption","text","title","description"):
                if first.get(k):
                    preview = str(first[k])[:80].replace(chr(10)," ")
                    print(f"    sample {k}: {preview}…")
                    break
        return items
    except Exception as e:
        print(f"    !! request failed: {type(e).__name__}: {e}")
        return []

def render_with_browser(url):
    """Fetch a URL through Apify web-scraper (real browser, runs JS) so we can read
    pages whose events only appear after JavaScript renders. Returns rendered HTML, or "" on failure.
    Used ONLY for URLs in JS_RENDER_URLS — other sources keep the cheap HTTP path."""
    if not APIFY_TOKEN:
        return ""
    # web-scraper needs a pageFunction returning JSON. We grab outerHTML + scroll to load lazy content.
    page_function = (
        "async function pageFunction(context){"
        "  const {page,log} = context;"
        "  // give JS a moment to render, then scroll to trigger any lazy loading"
        "  try{ await page.waitForTimeout(2500); }catch(e){}"
        "  try{ await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight)); }catch(e){}"
        "  try{ await page.waitForTimeout(1500); }catch(e){}"
        "  const html = await page.content();"
        "  return { html };"
        "}"
    )
    payload = {
        "startUrls": [{"url": url}],
        "pageFunction": page_function,
        "maxRequestRetries": 1,
        "maxPagesPerCrawl": 1,
        "useChrome": True,
        "headless": True,
    }
    try:
        items = run_actor(JS_RENDER_ACTOR, payload)
        if items and items[0].get("html"):
            return items[0]["html"]
    except Exception as e:
        print(f"    !! JS render failed: {type(e).__name__}: {e}")
    return ""

def scrape_instagram():
    print("Scraping Instagram…")
    payload = {
        # The official actor scrapes posts from hashtag PAGE urls in one run:
        "directUrls": ([f"https://www.instagram.com/explore/tags/{h}/" for h in IG_HASHTAGS]
                       + list(IG_PROFILES)),
        "resultsType": "posts",
        "resultsLimit": POSTS_PER_QUERY,
        "onlyPostsNewerThan": f"{MAX_POST_AGE_DAYS} days",
        "addParentData": False,
    }
    # Logged-in cookie dramatically improves hashtag results (often required now)
    if IG_SESSION_COOKIE:
        sid = IG_SESSION_COOKIE.split("sessionid=")[-1].strip().strip(";")
        payload["sessionCookies"] = [{"name":"sessionid","value":sid,"domain":".instagram.com"}]
        # Diagnostic: a valid sessionid is long (40+ chars) and contains "%3A".
        looks_valid = len(sid) >= 30 and "%3A" in sid
        print(f"  (using Instagram session cookie — length {len(sid)}, "
              f"{'looks valid ✓' if looks_valid else 'WARNING: looks too short / wrong cookie ✗'})")
    else:
        print("  (no IG session cookie set — hashtag results may be empty; see config note)")
    items = run_actor(IG_ACTOR, payload)
    posts = []
    skipped_no_caption = 0
    for it in items:
        # Different IG actors name the caption field differently — try them all
        cap = (it.get("caption") or it.get("text") or it.get("title")
               or it.get("description") or it.get("edge_media_to_caption") or "")
        if isinstance(cap, dict):   # some actors nest caption in an object
            cap = cap.get("text") or cap.get("caption") or ""
        # real post permalink — IG actor returns shortCode; build a clean /p/ link as fallback
        link = it.get("url") or it.get("postUrl") or it.get("inputUrl") or ""
        if not link and it.get("shortCode"):
            link = f"https://www.instagram.com/p/{it['shortCode']}/"
        img = it.get("displayUrl") or it.get("imageUrl")
        if not img and isinstance(it.get("images"), list) and it["images"]:
            img = it["images"][0]
        # Keep the post if it has a caption OR an image (image-only flyers → vision reads them)
        if not cap and not img:
            skipped_no_caption += 1
            continue
        posts.append({
            "caption": cap,
            "url": link,
            "image": img or "",
            "platform": "Instagram",
            "timestamp": it.get("timestamp") or it.get("takenAt"),
        })
    print(f"  → {len(posts)} Instagram posts"
          + (f"  ({skipped_no_caption} skipped — no caption or image)" if skipped_no_caption else ""))
    if skipped_no_caption and not posts:
        print("    !! All items skipped for missing caption. The actor uses a DIFFERENT field name.")
        print("       Look at the 'sample keys' line above to see the real field names.")
    return posts

def _fb_post(it):
    """Normalise one Facebook dataset item into our shape. Handles multiple actor formats."""
    cap = (it.get("text") or it.get("message") or it.get("message_text")
           or it.get("postText") or it.get("content") or "")
    # canonical post permalink across the common actors
    link = (it.get("url") or it.get("topLevelUrl") or it.get("postUrl")
            or it.get("facebookUrl") or it.get("link") or "")
    # image across known field shapes
    img = it.get("thumb") or it.get("imageUrl") or it.get("image") or ""
    if not img and isinstance(it.get("media"), list) and it["media"]:
        m0 = it["media"][0]
        img = (m0.get("thumbnail") or m0.get("image") or m0.get("url")
               or (m0.get("photo_image") or {}).get("uri") or "")
    if not img and isinstance(it.get("images"), list) and it["images"]:
        img = it["images"][0] if isinstance(it["images"][0], str) else ""
    # Keep if there's text OR an image (image-only flyers → vision reads them)
    if not cap and not img:
        return None
    return {
        "caption": cap,
        "url": link,
        "image": img or "",
        "platform": "Facebook",
        "timestamp": it.get("time") or it.get("timestamp") or it.get("created_at"),
    }

def scrape_facebook():
    print("Scraping Facebook…")
    posts = []

    # 0) SEARCH — keyword search across Facebook (like IG hashtag search).
    # Search ONE term at a time (one big comma-joined query tends to return 0 — FB reads it
    # as a single literal phrase). Splitting the budget across terms gets real results.
    if FB_SEARCH_ENABLED and FB_SEARCH_ACTOR and FB_SEARCH_ACTOR != "REPLACE_WITH_ACTOR_ID":
        start = (dt.date.today() - dt.timedelta(days=MAX_POST_AGE_DAYS)).isoformat()
        end = dt.date.today().isoformat()
        per_term = max(10, POSTS_PER_QUERY // max(1, len(SEARCH_TERMS)))
        got = 0; skipped = 0
        for term in SEARCH_TERMS:
            search_payload = {
                "query": term,                     # ONE keyword phrase at a time
                "search_type": "posts",
                "max_posts": per_term,
                "recent_posts": False,
                "start_date": start,
                "end_date": end,
            }
            for it in run_actor(FB_SEARCH_ACTOR, search_payload):
                p = _fb_post(it)
                if p: posts.append(p); got += 1
                else: skipped += 1
        print(f"  → {got} Facebook SEARCH posts"
              + (f"  ({skipped} skipped — no text)" if skipped else ""))
    else:
        print("  (FB search actor not set — fill FB_SEARCH_ACTOR to enable)")

    # 1) PAGES — point at specific Osho pages (reliable)
    if FB_PAGES:
        page_payload = {
            "startUrls": [{"url": u} for u in FB_PAGES],
            "maxPosts": POSTS_PER_QUERY,
            "onlyPostsNewerThan": f"{MAX_POST_AGE_DAYS} days",
        }
        skipped = 0
        got = 0
        for it in run_actor(FB_PAGES_ACTOR, page_payload):
            p = _fb_post(it)
            if p: posts.append(p); got += 1
            else: skipped += 1
        print(f"  → {got} Facebook PAGE posts"
              + (f"  ({skipped} skipped — no text)" if skipped else ""))
    else:
        print("  (no FB pages configured)")

    # 2) GROUPS — public Osho groups where camps get announced
    if FB_GROUPS:
        group_payload = {
            "startUrls": [{"url": u} for u in FB_GROUPS],
            "maxPosts": POSTS_PER_QUERY,
            "onlyPostsNewerThan": f"{MAX_POST_AGE_DAYS} days",
        }
        skipped = 0
        got = 0
        for it in run_actor(FB_GROUPS_ACTOR, group_payload):
            p = _fb_post(it)
            if p: posts.append(p); got += 1
            else: skipped += 1
        print(f"  → {got} Facebook GROUP posts"
              + (f"  ({skipped} skipped — no text)" if skipped else ""))
    else:
        print("  (no FB groups configured — add public group URLs to FB_GROUPS to include them)")

    print(f"  → {len(posts)} Facebook posts total")
    return posts


# ----------------------------------------------------------------------
# 2. EXTRACT  (Claude reads each caption -> structured event)
# ----------------------------------------------------------------------
TODAY = dt.date.today().isoformat()

EXTRACT_SYSTEM = (
    "You read social-media captions and decide if each one announces an UPCOMING Osho / "
    "meditation event (camp, retreat, workshop, or gathering). "
    "Today's date is " + TODAY + ". Reply with ONE JSON object only, no prose, no markdown.\n"
    "Schema: {is_event:bool, type:'Camp'|'Retreat'|'Workshop'|'Gathering'|null, "
    "title:str, start_date:'YYYY-MM-DD'|null, end_date:'YYYY-MM-DD'|null, venue:str|null, "
    "city:str|null, state:str|null, country:str|null, phone:str|null, organizer:str|null, "
    "description:str|null}\n"
    "Rules: is_event=false if it's not a real datable event, is a past recap, or is generic content. "
    "Infer the year if only a day/month is given (use the next future occurrence). "
    "state is the Indian state when country is India, else null. Keep description under 30 words. "
    "Captions may be in Hindi, Punjabi, Nepali, Marathi, Gujarati or other languages — read them, "
    "and OUTPUT all fields in English (translate title, venue, city, description). "
    "Recognise regional date formats. For Nepal, country='Nepal' and leave state null."
)

_extract_fail_count = [0]   # mutable counter shared across calls

def check_anthropic_key():
    """Verify the Anthropic key works BEFORE processing 130 posts. Fail loudly if not."""
    print("Checking Anthropic API key…")
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": EXTRACT_MODEL, "max_tokens": 10,
                  "messages": [{"role": "user", "content": "Reply with the single word OK"}]},
            timeout=30)
        if r.status_code == 200:
            print("  ✓ Anthropic key works.")
            return True
        print(f"  !! Anthropic key FAILED: HTTP {r.status_code} — {r.text[:300]}")
        print("     → Fix your ANTHROPIC_API_KEY secret and/or add billing credit at console.anthropic.com")
        return False
    except Exception as e:
        print(f"  !! Anthropic check error: {e}")
        return False

MAX_IMAGE_BYTES = 4 * 1024 * 1024   # 4 MB — Claude vision rejects larger images (HTTP 400)
VALID_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

def _download_image_b64(url):
    """Fetch an image URL and return (base64, media_type) or (None, None).
    Rejects images that are too large or in an unsupported format to prevent Claude HTTP 400."""
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200 or not r.content:
            return None, None
        # Reject oversized images — Claude vision returns HTTP 400 for images > ~5 MB
        if len(r.content) > MAX_IMAGE_BYTES:
            print(f"  ! image too large ({len(r.content)//1024}KB) — skipping vision for this post")
            return None, None
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        # Normalise common aliases
        if ctype in ("image/jpg",):
            ctype = "image/jpeg"
        if ctype not in VALID_IMAGE_TYPES:
            ctype = "image/jpeg"   # most IG/FB flyers are jpeg
        import base64
        return base64.standard_b64encode(r.content).decode(), ctype
    except Exception:
        return None, None

def extract_event(caption, image_url=None):
    # Decide whether to use vision: only when caption is thin AND we have an image.
    use_image = bool(VISION_FOR_IMAGE_POSTS and image_url
                     and len((caption or "").strip()) < VISION_MIN_CAPTION_LEN)
    content = []
    if use_image:
        b64, mtype = _download_image_b64(image_url)
        if b64:
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": mtype, "data": b64}})
            content.append({"type": "text",
                            "text": "This post has little caption text. Read the FLYER IMAGE above "
                                    "and extract the event details. Caption (may be empty): "
                                    + (caption or "")[:1000]})
        else:
            content = (caption or "")[:4000]   # image fetch failed → fall back to text
    else:
        content = (caption or "")[:4000]

    body = {
        "model": EXTRACT_MODEL,
        "max_tokens": 800,          # raised: prevents unterminated JSON on long captions
        "system": EXTRACT_SYSTEM,
        "messages": [{"role": "user", "content": content}],
    }
    last_err = None
    for attempt in range(2):   # try once, retry once on transient failure
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json=body, timeout=90)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:150]}"
                if r.status_code in (429, 500, 502, 503, 529) and attempt == 0:
                    time.sleep(2); continue       # transient — retry once
                break
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Truncated / malformed JSON — salvage the first complete {...} object
                m = _re.search(r"\{[^{}]*\}", text)
                if m:
                    try:
                        data = json.loads(m.group(0))
                    except Exception:
                        data = {"is_event": False}
                else:
                    data = {"is_event": False}
            # Defensive: Claude sometimes returns a list wrapping a single event.
            if isinstance(data, list):
                data = data[0] if data and isinstance(data[0], dict) else {"is_event": False}
            if not isinstance(data, dict):
                data = {"is_event": False}
            return data
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 0:
                time.sleep(2); continue            # timeout/network — retry once
    _extract_fail_count[0] += 1
    if _extract_fail_count[0] <= 3:
        print(f"  ! extraction failed after retry: {last_err}")
    return {"is_event": False}


def extract_event_from_file(path):
    """Read a local flyer image file with Claude vision → event dict.
    Handles single-event AND multi-event calendar images (returns first event for
    single-event callers; multi-event path uses extract_events_from_file instead)."""
    import base64, mimetypes
    try:
        with open(path, "rb") as f:
            raw = f.read()
        # Guard: skip files Claude vision can't process (too large or wrong format)
        if len(raw) > MAX_IMAGE_BYTES:
            print(f"  ! {os.path.basename(path)}: image too large ({len(raw)//1024}KB) — skipping")
            return {"is_event": False}
        mtype = mimetypes.guess_type(path)[0] or "image/jpeg"
        if mtype not in VALID_IMAGE_TYPES:
            mtype = "image/jpeg"
        b64 = base64.standard_b64encode(raw).decode()
    except Exception as e:
        print(f"  ! couldn't read flyer {path}: {e}")
        return {"is_event": False}

    body = {
        "model": EXTRACT_MODEL, "max_tokens": 900, "system": EXTRACT_SYSTEM,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mtype, "data": b64}},
            {"type": "text", "text": "This is a camp/event FLYER image. Read it and extract the event details. "
                                     "Return ONLY a single JSON object. If this image contains MULTIPLE events "
                                     "(e.g. a calendar), extract only the FIRST upcoming event."},
        ]}],
    }
    for attempt in range(2):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"}, json=body, timeout=90)
            if r.status_code != 200:
                if r.status_code in (429, 500, 502, 503, 529) and attempt == 0:
                    time.sleep(2); continue
                print(f"  ! flyer vision HTTP {r.status_code}: {r.text[:150]}")
                break
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Salvage first complete JSON object
                m = _re.search(r"\{[^{}]*\}", text)
                if m:
                    try:
                        data = json.loads(m.group(0))
                    except Exception:
                        data = {"is_event": False}
                else:
                    data = {"is_event": False}
            if isinstance(data, list):
                data = data[0] if data and isinstance(data[0], dict) else {"is_event": False}
            if not isinstance(data, dict):
                data = {"is_event": False}
            return data
        except Exception as e:
            if attempt == 0:
                time.sleep(2); continue
            print(f"  ! flyer vision failed: {e}")
    return {"is_event": False}

def extract_events_from_file(path):
    """Read a local flyer that may contain MULTIPLE events (e.g. a yearly calendar poster).
    Returns a LIST of event dicts. Falls back to single-event extraction if needed."""
    import base64, mimetypes
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > MAX_IMAGE_BYTES:
            print(f"  ! {os.path.basename(path)}: image too large ({len(raw)//1024}KB) — skipping")
            return []
        mtype = mimetypes.guess_type(path)[0] or "image/jpeg"
        if mtype not in VALID_IMAGE_TYPES:
            mtype = "image/jpeg"
        b64 = base64.standard_b64encode(raw).decode()
    except Exception as e:
        print(f"  ! couldn't read flyer {path}: {e}")
        return []

    MULTI_SYSTEM = (
        "You read event flyer images. Today is " + TODAY + ". "
        "Reply with ONLY a JSON array of event objects, no prose, no markdown. "
        "Each object: {is_event:true, type:'Camp'|'Retreat'|'Workshop'|'Gathering', "
        "title:str, start_date:'YYYY-MM-DD'|null, end_date:'YYYY-MM-DD'|null, "
        "venue:str|null, city:str|null, state:str|null, country:str|null, "
        "phone:str|null, organizer:str|null, description:str|null}. "
        "Extract ALL events visible in the image, including from calendars/posters with multiple camps. "
        "Infer year from context (2026 if not stated). Keep description under 25 words. "
        "Translate non-English text to English. If only one event, return a single-item array."
    )
    body = {
        "model": EXTRACT_MODEL, "max_tokens": 2000,
        "system": MULTI_SYSTEM,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mtype, "data": b64}},
            {"type": "text", "text": "Extract ALL events from this flyer/calendar image. Return a JSON array."},
        ]}],
    }
    for attempt in range(2):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"}, json=body, timeout=120)
            if r.status_code != 200:
                if r.status_code in (429, 500, 502, 503, 529) and attempt == 0:
                    time.sleep(3); continue
                print(f"  ! multi-flyer vision HTTP {r.status_code}: {r.text[:150]}")
                return []
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Salvage individual complete {...} objects from partial response
                data = []
                for m in _re.finditer(r"\{[^{}]+\}", text):
                    try:
                        data.append(json.loads(m.group(0)))
                    except Exception:
                        pass
                if data:
                    print(f"  (recovered {len(data)} events from partial JSON)")
            if isinstance(data, dict):
                data = [data]   # single event returned as object — wrap it
            if not isinstance(data, list):
                return []
            return [d for d in data if isinstance(d, dict) and d.get("is_event")]
        except Exception as e:
            if attempt == 0:
                time.sleep(3); continue
            print(f"  ! multi-flyer vision failed: {e}")
    return []



    return (s or "").replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\N", " ").strip()

def _ical_date(val):
    """Parse an iCal DTSTART/DTEND value → 'YYYY-MM-DD'. Handles 20260620 and 20260620T090000Z."""
    val = (val or "").strip()
    digits = _re.sub(r"[^0-9]", "", val)
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""

def read_html_event_pages():
    """Fetch each centre's events page and have Claude extract ALL upcoming camps from it.
    Works for ANY site (incl. custom-built ones with no feed). One Claude call per page.
    Also captures each camp's OWN image and re-hosts it so it shows on the card.

    ONCE-A-DAY CACHE: each page is fetched at most once per calendar day. If you run the
    workflow again the same day, the saved copy is reused (no re-fetch, no Claude cost, no
    risk of the site blocking us). Fresh fetch happens automatically on the next day."""
    out = []
    if not HTML_EVENT_PAGES:
        return out
    cache_dir = "feed_cache"
    os.makedirs(cache_dir, exist_ok=True)
    today = dt.date.today().isoformat()
    for entry in HTML_EVENT_PAGES:
        url, country, organizer = entry[0], entry[1], entry[2]
        contact_phone = entry[3] if len(entry) > 3 else None
        venue_addr = entry[4] if len(entry) > 4 else ""

        # --- once-a-day cache check ---
        cache_file = os.path.join(cache_dir, hashlib.md5(url.encode()).hexdigest()[:12] + ".json")
        cached = None
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cached = json.load(f)
            except Exception:
                cached = None
        if cached and cached.get("date") == today:
            evs = cached.get("events", [])
            out.extend(evs)
            print(f"  → {len(evs)} events from {organizer} (cached today, not re-fetched)")
            continue
        page_events = []  # collect this page's events to cache
        try:
            if url in JS_RENDER_URLS:
                # JavaScript-rendered page — fetch via Apify browser so events actually load
                print(f"  → using JS renderer for {organizer}")
                raw_html = render_with_browser(url)
                if not raw_html:
                    print(f"  ! {organizer}: JS render returned empty — skipping")
                    continue
            else:
                # Polite fetch: a referer + small delay + one retry reduces 403 bot-blocking.
                hdrs = dict(BROWSER_HEADERS)
                hdrs["Referer"] = url.rsplit("/", 1)[0] + "/"
                r = requests.get(url, timeout=45, headers=hdrs)
                if r.status_code == 403:
                    time.sleep(5)                       # brief cool-off, then one retry
                    r = requests.get(url, timeout=45, headers=hdrs)
                if r.status_code != 200:
                    print(f"  ! {organizer}: events page HTTP {r.status_code} "
                          f"(site is blocking automated requests — try again later)")
                    continue
                raw_html = r.text
            # Pull real image URLs in page order (so each camp matches its own picture).
            # Next.js wraps them as /_next/image?url=<ENCODED>&w=... — decode those too.
            import urllib.parse as _up
            imgs = []
            for m in _re.finditer(r'/_next/image\?url=([^&"]+)', raw_html):
                dec = _up.unquote(m.group(1))
                if any(k in dec for k in ("/uploads/", "/wp-content/", "/content/")):
                    imgs.append(dec)
            # Match content/upload image URLs (covers WordPress wp-content/uploads, and
            # Humaniversity's /content/ uploads). Skip logos/icons/sprites.
            for m in _re.finditer(
                r'(https?://[^\s"\']+/(?:wp-content/uploads|uploads|content/uploads|content)/[^\s"\'?)]+\.(?:jpg|jpeg|png|webp))',
                raw_html, _re.IGNORECASE):
                u = m.group(1)
                low = u.lower()
                if any(skip in low for skip in ("logo", "icon", "favicon", "sprite", "cropped-", "mstile", "header-image")):
                    continue
                if u not in imgs:
                    imgs.append(u)
            # text for Claude
            text = _re.sub(r"<script[\s\S]*?</script>", " ", raw_html)
            text = _re.sub(r"<style[\s\S]*?</style>", " ", text)
            text = _re.sub(r"<[^>]+>", " ", text)
            text = _re.sub(r"\s+", " ", text).strip()
            # Some sites (e.g. osho.com) bury the real schedule AFTER a huge nav menu.
            # If we can find where the actual course list starts, drop everything before it
            # so the dated events aren't pushed past the character limit.
            for marker in ("Monthly Schedules", "SELECT MONTH", "Upcoming Courses",
                           "Upcoming  Courses", "Our Calendar", "upcoming workshops"):
                pos = text.find(marker)
                if pos > 0:
                    text = text[pos:]
                    break
            # Also cut trailing noise (reviews/FAQs/footer) that comes AFTER the course list,
            # so the extractor focuses on the dated courses, not testimonials.
            for endmarker in ("About  OSHO", "About OSHO Himalayas", "Our Reviews",
                              "Why Meditators", "Frequently Asked", "Got Questions",
                              "About Us About", "Dr. Wiardi Beckmanlaan", "Newsletter",
                              "Pin It on Pinterest", "Share This"):
                epos = text.find(endmarker)
                if epos > 500:           # keep at least the courses before cutting
                    text = text[:epos]
                    break
            text = text[:24000]   # higher limit so long calendars (30-50 dated courses) fit
        except Exception as e:
            print(f"  ! {organizer}: fetch failed ({type(e).__name__})")
            continue
        prompt = (
            f"Today is {TODAY}. Below is the text of an Osho centre's events page. "
            "Extract EVERY upcoming meditation camp/retreat/workshop/celebration with a clear date, "
            "IN THE ORDER they appear on the page. The events may run together in dense text "
            "without line breaks between them — be careful to find every dated entry "
            "(look for date patterns like '5 - 7 Jun 2026' or '25 Jun – 1 Jul 2026'). "
            "Reply with ONLY a JSON array, each item: "
            "{title, title_original, start_date:'YYYY-MM-DD', end_date:'YYYY-MM-DD', description}. "
            "If the page is NOT in English, set title to a clear English translation and "
            "title_original to the exact original-language title. If the page is already in English, "
            "set title_original to an empty string. "
            "Keep description under 12 words, in English. "
            "Infer the year from context (events are 2026 unless stated). No prose, just the JSON array.\n\n"
            + text
        )
        try:
            resp = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": EXTRACT_MODEL, "max_tokens": 8000,
                      "messages": [{"role": "user", "content": prompt}]}, timeout=120)
            if resp.status_code != 200:
                print(f"  ! {organizer}: Claude HTTP {resp.status_code}")
                continue
            rawtext = "".join(b.get("text", "") for b in resp.json().get("content", []))
            rawtext = rawtext.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                items = json.loads(rawtext)
            except json.JSONDecodeError:
                # Response was likely truncated mid-array — salvage each complete {...} object.
                items = []
                for m in _re.finditer(r"\{[^{}]*\}", rawtext):
                    try:
                        items.append(json.loads(m.group(0)))
                    except Exception:
                        pass
                if items:
                    print(f"  (recovered {len(items)} events from a long {organizer} page)")
        except Exception as e:
            print(f"  ! {organizer}: extraction failed ({type(e).__name__})")
            continue
        got = 0
        items = items if isinstance(items, list) else []
        for idx, it in enumerate(items):
            title = (it.get("title") or "").strip()
            title_original = (it.get("title_original") or "").strip()
            start = (it.get("start_date") or "").strip()
            if not title or not start:
                continue
            # Match this camp to its image by position on the page, then re-host it.
            img_src = imgs[idx] if idx < len(imgs) else ""
            flyer = rehost_image(img_src, img_src) if img_src else ""
            # City/state per centre (helps the India "by State" filter group it correctly).
            if "Dham" in organizer:
                city, state = "New Delhi", "Delhi"
            elif "Himalayas" in organizer:
                city, state = "Dharamshala", "Himachal Pradesh"
            elif "Nisarga" in organizer:
                city, state = "Dharamshala", "Himachal Pradesh"
            elif "Nargol" in organizer:
                city, state = "Nargol", "Gujarat"
            elif "Ramana" in organizer:
                city, state = "Tiruvannamalai", "Tamil Nadu"
            elif "Risk" in organizer:
                city, state = "Braedstrup", None
            elif "Uta" in organizer:
                city, state = "Köln", None
            elif "Humaniversity" in organizer:
                city, state = "Egmond aan Zee", None
            else:
                city, state = "", None
            ev_obj = {
                "is_event": True,
                "type": "Camp" if "camp" in title.lower() else ("Retreat" if "retreat" in title.lower() else "Workshop"),
                "title": title,
                "title_original": title_original,
                "start_date": start,
                "end_date": (it.get("end_date") or start).strip(),
                "venue": venue_addr or organizer, "city": city,
                "state": state, "country": country, "phone": contact_phone, "organizer": organizer,
                "description": (it.get("description") or "")[:200],
                "source_url": url, "source_platform": f"{organizer} (website)",
                "flyer_url": flyer, "region": REGION_MAP.get(country, "Asia"),
            }
            out.append(ev_obj)
            page_events.append(ev_obj)
            got += 1
        print(f"  → {got} events from {organizer} (web page)")
        # Save today's results so later runs today reuse them (once-a-day fetch).
        if got > 0:
            try:
                with open(cache_file, "w") as f:
                    json.dump({"date": today, "events": page_events}, f)
            except Exception:
                pass
    return out

def read_ical_feeds():
    """Read events from standard iCal (.ics) feeds — works for any centre that publishes one.
    FREE: no Apify, no AI. Returns list of event dicts."""
    out = []
    if not ICAL_FEEDS:
        return out
    for url, country, organizer in ICAL_FEEDS:
        try:
            r = requests.get(url, timeout=45, headers=BROWSER_HEADERS)
            ctype = r.headers.get("content-type", "").lower()
            # Accept if status 200 AND (content-type is calendar OR body starts with VCALENDAR)
            is_ical = (r.status_code == 200 and
                       ("calendar" in ctype or "BEGIN:VCALENDAR" in r.text[:500]))
            if not is_ical:
                hint = ""
                if r.status_code == 403:
                    hint = " (site is blocking bots — try fetching manually to confirm URL)"
                elif r.status_code == 200:
                    hint = f" (got HTML, not iCal — content-type: {ctype[:60]})"
                print(f"  ! {organizer}: no iCal feed (HTTP {r.status_code}){hint}")
                continue
            text = r.text.replace("\r\n ", "").replace("\r\n\t", "")  # unfold long lines
        except Exception as e:
            print(f"  ! {organizer}: iCal fetch failed ({type(e).__name__})")
            continue
        got = 0
        for block in text.split("BEGIN:VEVENT")[1:]:
            block = block.split("END:VEVENT")[0]
            fields = {}
            for line in block.splitlines():
                if ":" in line:
                    key = line.split(":", 1)[0].split(";")[0].strip().upper()
                    valpart = line.split(":", 1)[1]
                    fields.setdefault(key, valpart)
            title = _ical_unescape(fields.get("SUMMARY", ""))
            start = _ical_date(fields.get("DTSTART", ""))
            end = _ical_date(fields.get("DTEND", "")) or start
            if not title or not start:
                continue
            loc = _ical_unescape(fields.get("LOCATION", ""))
            desc = _ical_unescape(fields.get("DESCRIPTION", ""))[:200]
            city = loc.split(",")[0].strip() if loc else ""
            out.append({
                "is_event": True,
                "type": "Camp" if "camp" in title.lower() else ("Retreat" if "retreat" in title.lower() else "Workshop"),
                "title": title, "start_date": start, "end_date": end,
                "venue": loc or organizer, "city": city or "", "state": None,
                "country": country, "phone": None, "organizer": organizer,
                "description": desc,
                "source_url": _ical_unescape(fields.get("URL", "")) or url.split("/?")[0].split("/events")[0],
                "source_platform": f"{organizer} (website)",
                "flyer_url": "", "region": REGION_MAP.get(country, "Asia"),
            })
            got += 1
        print(f"  → {got} events from {organizer} (iCal)")
    return out

def read_wp_event_sites():
    """Read events from WordPress 'The Events Calendar' JSON APIs (e.g. Tapoban).
    These give clean structured data — no Apify, no AI cost. Returns list of event dicts."""
    out = []
    if not WP_EVENT_SITES:
        return out
    import html as _html
    today = dt.date.today().isoformat()
    for site, default_country in WP_EVENT_SITES:
        url = f"{site}/wp-json/tribe/events/v1/events?per_page=50&start_date={today}&status=publish"
        try:
            r = requests.get(url, timeout=45, headers=BROWSER_HEADERS)
            if r.status_code != 200:
                print(f"  ! {site} events API HTTP {r.status_code}")
                continue
            data = r.json()
        except Exception as e:
            print(f"  ! {site} events API failed: {type(e).__name__}")
            continue
        items = data.get("events", []) if isinstance(data, dict) else []
        got = 0
        for it in items:
            try:
                title = _html.unescape((it.get("title") or "").strip())
                start = (it.get("start_date") or "")[:10]      # 'YYYY-MM-DD HH:MM:SS' → date
                end = (it.get("end_date") or it.get("start_date") or "")[:10]
                if not title or not start:
                    continue
                venue_obj = it.get("venue") or {}
                venue = _html.unescape(venue_obj.get("venue", "") or "") if isinstance(venue_obj, dict) else ""
                city = ""
                if isinstance(venue_obj, dict):
                    city = venue_obj.get("city") or ""
                desc = _html.unescape(_re.sub("<[^>]+>", "", it.get("description") or ""))[:200].strip()
                ev = {
                    "is_event": True,
                    "type": "Camp" if "camp" in title.lower() else "Retreat",
                    "title": title,
                    "start_date": start, "end_date": end,
                    "venue": venue or "Osho Tapoban",
                    "city": city or "Kathmandu",
                    "state": None,
                    "country": default_country,
                    "phone": None,
                    "organizer": "Osho Tapoban",
                    "description": desc,
                    "source_url": it.get("url") or site,
                    "source_platform": "Tapoban (website)",
                    "flyer_url": (it.get("image") or {}).get("url", "") if isinstance(it.get("image"), dict) else "",
                    "region": REGION_MAP.get(default_country, "Asia"),
                }
                out.append(ev); got += 1
            except Exception:
                continue
        print(f"  → {got} events from {site}")
    return out

def read_local_flyers():
    """Read every image in FLYERS_DIR with Claude vision.
    Handles BOTH single-event flyers and multi-event calendar posters.
    CACHED: Claude vision is only called for new or modified files.
    Cache key = filename|mtime — so editing a flyer triggers re-extraction.
    Returns list of event dicts."""
    import glob
    if not os.path.isdir(FLYERS_DIR):
        print(f"  (no '{FLYERS_DIR}/' folder — add flyer images there to include them)")
        return []
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG", "*.JPEG", "*.PNG"):
        files += glob.glob(os.path.join(FLYERS_DIR, ext))
    if not files:
        print(f"  (no flyer images in '{FLYERS_DIR}/')")
        return []

    flyer_cache = _load_flyer_cache()
    cached_count = 0
    fresh_count = 0

    print(f"Reading {len(files)} local flyer image(s) with Claude vision…")
    events = []
    for path in files:
        try:
            import urllib.parse
            fname = urllib.parse.quote(os.path.basename(path))
            flyer_public_url = FLYERS_BASE_URL + fname

            # Cache key: filename + MD5 of file contents
            # mtime changes on every fresh git checkout, so we use content hash instead.
            # If the file is replaced/edited, hash changes → cache miss → re-extracted
            import hashlib as _hl
            file_hash = _hl.md5(open(path, "rb").read()).hexdigest()[:16]
            cache_key = os.path.basename(path) + "|" + file_hash

            if cache_key in flyer_cache:
                # Serve from cache — zero Claude cost
                extracted = flyer_cache[cache_key]
                cached_count += 1
                hit_label = "(cached)"
            else:
                # New or modified file — call Claude vision
                extracted = extract_events_from_file(path)
                flyer_cache[cache_key] = extracted  # persist result
                fresh_count += 1
                hit_label = ""

            if not extracted:
                print(f"  – {os.path.basename(path)} → not a datable event {hit_label}".strip())
                continue

            if len(extracted) > 1:
                print(f"  ✓ {os.path.basename(path)} → {len(extracted)} events (multi-event flyer) {hit_label}".strip())
            else:
                print(f"  ✓ {os.path.basename(path)} → {extracted[0].get('title','(event)')} {hit_label}".strip())

            for idx, ev in enumerate(extracted):
                ev["source_platform"] = "Flyer upload"
                ev["source_url"] = ""
                ev["flyer_url"] = flyer_public_url
                ev["country"] = ev.get("country") or "India"
                ev["region"] = REGION_MAP.get(ev["country"], "Asia")
                ev["_flyer_path"] = path
                # Stable id: filename + index so each event from a multi-event flyer is unique
                id_key = os.path.basename(path) + f"_{idx}"
                ev["_fixed_id"] = "fly" + hashlib.md5(id_key.encode()).hexdigest()[:9]
                events.append(ev)
        except Exception as e:
            print(f"  ! {os.path.basename(path)} → skipped ({type(e).__name__})")
            continue

    _save_flyer_cache(flyer_cache)
    print(f"  📁 Flyer cache: {cached_count} served from cache, {fresh_count} newly extracted by Claude")
    return events

def make_id(ev):
    """Stable dedup id that survives across runs.
    - Flyer uploads & re-hosted images: id from the IMAGE FILENAME (stored in flyer_url),
      so a carried-forward copy and a freshly-read copy always get the SAME id → no repeats.
    - Everything else: exact title + start date + city."""
    fu = ev.get("flyer_url") or ""
    # Our own repo images (flyers/ or card_images/) have stable filenames — key off them.
    if "/flyers/" in fu or "/card_images/" in fu:
        fname = fu.rstrip("/").split("/")[-1]
        return "img" + hashlib.md5(fname.encode()).hexdigest()[:9]
    if ev.get("_fixed_id"):
        return ev["_fixed_id"]
    title = (ev.get("title") or "").strip().lower()
    city = (ev.get("city") or ev.get("venue") or "").strip().lower()
    sd = (ev.get("start_date") or "").strip()
    return hashlib.md5(f"{title}|{sd}|{city}".encode()).hexdigest()[:12]

def keep_upcoming(ev):
    start = ev.get("start_date")
    end = ev.get("end_date") or start
    if not end:
        return False
    try:
        end_d = dt.date.fromisoformat(end)
    except ValueError:
        return False
    # must not have already passed
    if end_d < dt.date.today():
        return False
    # Drop long / open-ended entries (e.g. daily meditations marked "all year/all month").
    # We only want actual dated camps of 28 days or less.
    if start:
        try:
            start_d = dt.date.fromisoformat(start)
            if (end_d - start_d).days > 28:
                return False
        except ValueError:
            pass
    return True

def smart_crop_hint(img_path_or_bytes):
    """Ask Claude to analyse an image and return a crop hint dict so the card renderer
    can display faces/subjects correctly without cutting off heads.

    Returns a dict:
      {
        "has_person": bool,
        "face_y_pct": float,   # 0-100: how far down the image the face centre is (%)
        "focus_y_pct": float,  # 0-100: best vertical centre for a portrait crop (%)
        "object_fit": "top"|"center"|"bottom",  # CSS object-position equivalent
        "note": str            # short human-readable reason
      }
    Returns None on any failure (caller falls back to default "center" crop).
    """
    import base64, mimetypes
    try:
        if isinstance(img_path_or_bytes, (str, bytes.__class__)) and not isinstance(img_path_or_bytes, bytes):
            # it's a file path
            with open(img_path_or_bytes, "rb") as f:
                raw = f.read()
        else:
            raw = img_path_or_bytes

        if not raw or len(raw) > MAX_IMAGE_BYTES:
            return None

        mtype = "image/jpeg"
        if raw[:4] == b'\x89PNG':
            mtype = "image/png"
        elif raw[:4] == b'RIFF' or raw[8:12] == b'WEBP':
            mtype = "image/webp"

        b64 = base64.standard_b64encode(raw).decode()

        body = {
            "model": EXTRACT_MODEL,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mtype, "data": b64}},
                {"type": "text", "text": (
                    "Analyse this image for smart cropping on an event card. "
                    "Reply with ONLY a JSON object, no prose:\n"
                    "{\n"
                    '  "has_person": true/false,\n'
                    '  "face_y_pct": <0-100, vertical % where face centre sits, null if no face>,\n'
                    '  "focus_y_pct": <0-100, best vertical centre for a 3:4 portrait crop>,\n'
                    '  "object_fit": "top" or "center" or "bottom",\n'
                    '  "note": "<10 words why>"\n'
                    "}\n"
                    "Examples: headshot at top → face_y_pct≈20, focus_y_pct≈25, object_fit=top. "
                    "Full-body standing → face_y_pct≈15, focus_y_pct≈20, object_fit=top. "
                    "Group photo → focus_y_pct≈40, object_fit=center."
                )}
            ]}]
        }
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=body, timeout=30)
        if r.status_code != 200:
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        hint = json.loads(text)
        if not isinstance(hint, dict):
            return None
        # Normalise
        hint.setdefault("has_person", False)
        hint.setdefault("focus_y_pct", 40)
        hint.setdefault("object_fit", "center")
        hint["focus_y_pct"] = max(0, min(100, float(hint.get("focus_y_pct") or 40)))
        return hint
    except Exception:
        return None


# Crop-hint cache file — so we don't re-analyse the same image every run.
CROP_HINT_CACHE_FILE = os.path.join("feed_cache", "crop_hints.json")

# Flyer vision cache — so Claude is NOT called again for unchanged flyer files.
# Key = "filename|mtime". New/modified flyers are detected automatically.
FLYER_CACHE_FILE = os.path.join("feed_cache", "flyer_cache.json")

def _load_crop_cache():
    try:
        with open(CROP_HINT_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_crop_cache(cache):
    try:
        os.makedirs("feed_cache", exist_ok=True)
        with open(CROP_HINT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def _load_flyer_cache():
    try:
        with open(FLYER_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_flyer_cache(cache):
    try:
        os.makedirs("feed_cache", exist_ok=True)
        with open(FLYER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def rehost_image(img_url, key):
    """Download an external image into CARD_IMG_DIR and return our own permanent URL.
    'key' makes a stable filename so the same source reuses the same file.
    Preserves the real file extension so browsers render it. Returns '' on failure."""
    if not img_url:
        return ""
    try:
        os.makedirs(CARD_IMG_DIR, exist_ok=True)
        # Detect a sensible extension from the source URL (default jpg).
        ext = "jpg"
        m = _re.search(r"\.(jpe?g|png|webp)(?:[?#]|$)", img_url, _re.I)
        if m:
            ext = m.group(1).lower().replace("jpeg", "jpg")
        name = "img" + hashlib.md5((key or img_url).encode()).hexdigest()[:12] + "." + ext
        path = os.path.join(CARD_IMG_DIR, name)
        if not os.path.exists(path):                 # don't re-download if we already have it
            r = requests.get(img_url, timeout=30, headers=BROWSER_HEADERS)
            if r.status_code != 200 or not r.content or len(r.content) < 500:
                return ""
            # If the server says it's an image type, trust that for the extension.
            ctype = r.headers.get("Content-Type", "").lower()
            if "webp" in ctype: real = "webp"
            elif "png" in ctype: real = "png"
            elif "jpeg" in ctype or "jpg" in ctype: real = "jpg"
            else: real = ext
            if real != ext:
                name = "img" + hashlib.md5((key or img_url).encode()).hexdigest()[:12] + "." + real
                path = os.path.join(CARD_IMG_DIR, name)
            with open(path, "wb") as f:
                f.write(r.content)
        return CARD_IMG_BASE_URL + name
    except Exception:
        return ""

UNSPLASH_CACHE_FILE = os.path.join("feed_cache", "unsplash_cache.json")

def _load_unsplash_cache():
    try:
        with open(UNSPLASH_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_unsplash_cache(cache):
    try:
        os.makedirs("feed_cache", exist_ok=True)
        with open(UNSPLASH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def unsplash_image_for(title, cache):
    """Return a themed Unsplash image URL for a camp title, searched ONCE and cached
    forever (keyed by title). Searches api.unsplash.com only for titles never seen
    before, so we stay well under the 50/hour demo limit. Returns '' if no key / no result.
    Note: displaying images.unsplash.com URLs does NOT count against the rate limit —
    only the search call here does, and that's one-per-new-title, cached permanently."""
    if not UNSPLASH_KEY or not title:
        return ""
    key = title.strip().lower()
    if key in cache:                       # already searched once → reuse forever
        return cache[key]
    # Build a focused query from the title (drop generic filler words).
    q = _re.sub(r"\b(osho|meditation|camp|retreat|shivir|the|of|a|an|with|days?|by)\b", " ",
                title, flags=_re.I)
    q = _re.sub(r"[^a-zA-Z ]", " ", q).strip()
    query = (q + " meditation spiritual").strip() or "meditation"
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 1, "orientation": "portrait",
                    "content_filter": "high"},
            headers={"Authorization": "Client-ID " + UNSPLASH_KEY},
            timeout=20,
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                urls = results[0].get("urls", {})
                # Build an exact 6x4 PORTRAIT crop (800w x 1200h) from the raw URL so the
                # image always arrives taller-than-wide and fills the card with no letterbox.
                raw = urls.get("raw", "")
                if raw:
                    sep = "&" if "?" in raw else "?"
                    url = f"{raw}{sep}w=800&h=1200&fit=crop&crop=faces,center&q=70&auto=format"
                else:
                    url = urls.get("regular", "")
                cache[key] = url           # cache the result (even searched-this-run)
                return url
        cache[key] = ""                    # cache the miss too, so we don't re-search a dud
        return ""
    except Exception:
        return ""

def build():
    print("="*60)
    print(f"Token present: {'yes' if APIFY_TOKEN else 'NO — MISSING!'} "
          f"(len {len(APIFY_TOKEN)})  |  Anthropic key: {'yes' if ANTHROPIC_KEY else 'NO'}")
    print(f"Window: last {MAX_POST_AGE_DAYS} days  |  posts/query: {POSTS_PER_QUERY}")
    print("="*60)

    # FREE TEST MODE: set repo secret/variable TEST_MODE=1 to SKIP all Apify scraping.
    # This lets you test the git commit/push step WITHOUT spending any Apify credit.
    # It just keeps whatever is already in events.json and re-saves it.
    if os.environ.get("TEST_MODE") == "1":
        print("🧪 TEST_MODE on — skipping Apify entirely (no credit spent).")
        try:
            with open(OUTPUT_FILE, encoding="utf-8") as f:
                existing = json.load(f).get("events", [])
        except Exception:
            existing = []
        out = {"generated_at": dt.datetime.utcnow().isoformat() + "Z",
               "test_marker": dt.datetime.utcnow().isoformat() + "Z",  # forces a change so push is tested
               "events": existing}
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"✓ TEST_MODE: re-saved {len(existing)} existing events (0 Apify cost).")
        return

    # ONCE-A-DAY APIFY CACHE: scrape Instagram/Facebook at most once per calendar day.
    # Repeat runs the same day reuse the saved raw posts → no extra Apify charge.
    os.makedirs("feed_cache", exist_ok=True)
    posts_cache = os.path.join("feed_cache", "apify_posts.json")
    today_str = dt.date.today().isoformat()
    posts = None
    if os.path.exists(posts_cache):
        try:
            c = json.load(open(posts_cache))
            if c.get("date") == today_str:
                posts = c.get("posts", [])
                print(f"\n♻️  Reusing {len(posts)} Instagram/Facebook posts scraped earlier today "
                      f"(no extra Apify cost). Fresh scrape happens tomorrow.")
        except Exception:
            posts = None
    if posts is None:
        posts = scrape_instagram() + scrape_facebook()
        if posts:                                  # only cache a successful scrape
            try:
                json.dump({"date": today_str, "posts": posts}, open(posts_cache, "w"))
            except Exception:
                pass
    print(f"\nTOTAL posts scraped from all platforms: {len(posts)}")
    if not posts:
        print("⚠️  Zero posts scraped. The problem is APIFY (token, actor, or no posts for these tags),")
        print("    NOT Claude. Check the HTTP status / error body printed above.")

    key_ok = check_anthropic_key()
    if not key_ok:
        print("⚠️  Anthropic key not working — every post will be marked 'not an event'.")
        print("    THIS is why events.json is empty. Fix the key, then re-run.")

    print(f"\nExtracting {len(posts)} posts with Claude…")
    events, seen = [], set()
    n_is_event = n_upcoming = 0
    crop_cache = _load_crop_cache()
    n_crop_analysed = 0
    for i, p in enumerate(posts, 1):
        ev = extract_event(p["caption"], p.get("image"))
        if not ev.get("is_event"):
            continue
        n_is_event += 1
        ev["source_url"] = p["url"] if p.get("url") else ""
        ev["source_platform"] = p["platform"]
        # Re-host the post image on our repo so it actually shows on the card (IG/FB block embeds).
        ev["flyer_url"] = rehost_image(p.get("image"), p.get("url") or p.get("image"))
        # AI crop hint — analyse person photo once, cache forever by image URL
        if ev.get("flyer_url") and p.get("image"):
            cache_key = hashlib.md5((p.get("image") or "").encode()).hexdigest()[:16]
            if cache_key not in crop_cache:
                # Download the re-hosted local file to analyse (avoids re-fetching from IG/FB)
                local_name = ev["flyer_url"].split("/")[-1]
                local_path = os.path.join(CARD_IMG_DIR, local_name)
                if os.path.exists(local_path):
                    hint = smart_crop_hint(local_path)
                    crop_cache[cache_key] = hint or {}
                    if hint:
                        n_crop_analysed += 1
            ev["crop_hint"] = crop_cache.get(cache_key) or {}
        ev["country"] = ev.get("country") or "India"
        ev["region"] = REGION_MAP.get(ev["country"], "Asia")
        if not keep_upcoming(ev):
            continue
        n_upcoming += 1
        ev["id"] = make_id(ev)
        if ev["id"] in seen:                       # dedupe
            continue
        seen.add(ev["id"])
        events.append(ev)
    if n_crop_analysed:
        _save_crop_cache(crop_cache)
        print(f"  🖼  AI crop analysis: {n_crop_analysed} new images analysed ({len(crop_cache)} cached)")

    # --- WEBSITE EVENT FEEDS (iCal + WordPress) — international centres, free, no AI ---
    n_wp = 0
    print("\nReading website event feeds…")
    for ev in (read_ical_feeds() + read_wp_event_sites() + read_html_event_pages()):
        try:
            if not keep_upcoming(ev):
                continue
            ev["id"] = make_id(ev)
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            events.append(ev)
            n_wp += 1
        except Exception:
            continue

    # --- LOCAL FLYERS: your uploaded WhatsApp flyer images, read by Claude vision ---
    n_flyer = 0
    flyers_to_delete = []        # files for camps that are confirmed PAST (clean up after)
    for ev in read_local_flyers():
        try:
            if not keep_upcoming(ev):
                print(f"  – flyer event '{ev.get('title','')}' skipped as past "
                      f"(start={ev.get('start_date')}, end={ev.get('end_date')})")
                end = ev.get("end_date") or ev.get("start_date")
                if end and ev.get("_flyer_path"):
                    try:
                        if dt.date.fromisoformat(end) < dt.date.today():
                            flyers_to_delete.append(ev["_flyer_path"])
                    except ValueError:
                        pass
                continue
            # AI crop hint for the flyer image (cached by filename)
            flyer_path = ev.get("_flyer_path")
            if flyer_path:
                cache_key = "flyer_" + hashlib.md5(os.path.basename(flyer_path).encode()).hexdigest()[:16]
                if cache_key not in crop_cache:
                    hint = smart_crop_hint(flyer_path)
                    crop_cache[cache_key] = hint or {}
                    if hint:
                        n_crop_analysed += 1
                ev["crop_hint"] = crop_cache.get(cache_key) or {}
            ev["id"] = make_id(ev)
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            events.append(ev)
            n_flyer += 1
        except Exception as e:
            print(f"  ! flyer event skipped ({type(e).__name__})")
            continue

    # Delete flyer files for confirmed-past camps so they don't cost vision credit next run.
    if flyers_to_delete:
        deleted = 0
        for fp in set(flyers_to_delete):
            try:
                os.remove(fp); deleted += 1
            except Exception:
                pass
        print(f"  🗑  removed {deleted} past flyer image(s) from '{FLYERS_DIR}/'")

    # Persist crop hint cache so new analyses aren't lost between runs
    if n_crop_analysed:
        _save_crop_cache(crop_cache)

    events.sort(key=lambda e: e.get("start_date") or "9999")

    # --- ACCUMULATE: merge with what was found in previous runs ---
    # Load existing events.json, keep any still-upcoming events not in this batch,
    # so the directory GROWS over time instead of resetting to ~10 each run.
    existing = []
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            existing = json.load(f).get("events", [])
    except Exception:
        existing = []

    merged, seen_ids = [], set()

    # --- BLOCKLIST: load ONCE here so it blocks both new events AND carried-forward ones ---
    blockset = set()
    try:
        if os.path.exists("deleted.json"):
            with open("deleted.json", encoding="utf-8") as f:
                for x in json.load(f):
                    blockset.add(str(x).lower().strip())
    except Exception as ex:
        print(f"  ! blocklist load skipped: {ex}")

    def _norm(s):
        """Normalise a string for fuzzy blocklist matching — lowercase, strip punctuation/spaces."""
        import unicodedata
        s = (s or "").lower().strip()
        s = _re.sub(r"[^\w\s]", "", s)   # strip punctuation
        s = _re.sub(r"\s+", " ", s).strip()
        return s

    def _blocked(ev):
        """Return True if this event matches any entry in the blocklist.
        Matches on: exact ID, exact title|start|city key, OR normalised title+date alone
        (catches cases where city differs slightly or Claude reads title differently)."""
        eid = str(ev.get("id","")).lower().strip()
        title = _norm(ev.get("title",""))
        start = (ev.get("start_date","") or "").strip()
        city  = _norm(ev.get("city","") or ev.get("venue",""))
        full_key  = f"{title}|{start}|{city}"
        short_key = f"{title}|{start}"          # city-agnostic fallback
        return (eid in blockset or full_key in blockset or short_key in blockset)

    n_blocked_new = 0
    for ev in events:                       # new finds first (freshest data wins on dupes)
        ev["id"] = make_id(ev)              # recompute with aggressive dedup
        if _blocked(ev):
            n_blocked_new += 1
            continue
        if ev["id"] not in seen_ids:
            seen_ids.add(ev["id"]); merged.append(ev)
    carried = 0
    n_blocked_carried = 0
    for ev in existing:
        eid = make_id(ev)                    # recompute (ignore old weak IDs) so dupes collapse
        if eid in seen_ids:
            continue
        if _blocked(ev):                     # block carried-forward events too
            n_blocked_carried += 1
            continue
        if keep_upcoming(ev):               # only carry forward events that haven't passed
            ev["id"] = eid
            seen_ids.add(eid); merged.append(ev); carried += 1

    if n_blocked_new or n_blocked_carried:
        print(f"  🚫 Blocklist: removed {n_blocked_new} new + {n_blocked_carried} carried-forward event(s)")

    merged.sort(key=lambda e: e.get("start_date") or "9999")


    # searched once per title and cached forever (so visitors see varied images, and we
    # never hit the API rate limit). Only runs if UNSPLASH_ACCESS_KEY is set. ---
    if UNSPLASH_KEY:
        ucache = _load_unsplash_cache()

        # --- AUTO-ROTATION: replace old LANDSCAPE Unsplash images with true PORTRAIT ---
        # Runs for a limited number of times only (ROTATE_MAX_RUNS), then stops forever.
        # Each run, it deletes up to ROTATE_PER_RUN cached entries whose URL is an old
        # landscape image (no "h=1200"), so they get re-searched as portrait below.
        # A counter is stored inside the cache under the key "__rotation_runs_done__".
        ROTATE_PER_RUN  = 15
        ROTATE_MAX_RUNS = 12
        runs_done = 0
        try:
            runs_done = int(ucache.get("__rotation_runs_done__", 0))
        except Exception:
            runs_done = 0
        if runs_done < ROTATE_MAX_RUNS:
            # find cached entries that are still old landscape (real Unsplash, no h=1200)
            landscape_keys = [
                k for k, v in ucache.items()
                if isinstance(v, str) and "images.unsplash.com" in v and "h=1200" not in v
            ]
            to_drop = landscape_keys[:ROTATE_PER_RUN]
            for k in to_drop:
                ucache.pop(k, None)          # deleting → forces a fresh portrait search
            runs_done += 1
            ucache["__rotation_runs_done__"] = runs_done
            print(f"  🔄 Portrait auto-rotation: run {runs_done}/{ROTATE_MAX_RUNS} — "
                  f"dropped {len(to_drop)} old landscape image(s) for re-search "
                  f"({len(landscape_keys) - len(to_drop)} still remaining)")
            if runs_done >= ROTATE_MAX_RUNS:
                print("  ✅ Portrait auto-rotation finished — it will not run again.")

        searched = 0
        SEARCH_CAP = 45     # stay safely under Unsplash's 50/hour demo limit
        portrait_fixed = 0
        for ev in merged:
            fu = ev.get("flyer_url", "")
            title_key = ev.get("title", "").strip().lower()

            # FIX: event carries an OLD landscape Unsplash URL but cache already has
            # the portrait version — just apply it directly, no new API search needed.
            is_landscape_unsplash = (
                "images.unsplash.com" in fu and "h=1200" not in fu
            )
            if is_landscape_unsplash and title_key in ucache:
                ev["flyer_url"] = ucache[title_key]
                ev["_img_source"] = "unsplash"
                portrait_fixed += 1
                continue

            # Re-search if: (a) no image at all, OR (b) it's an Unsplash image whose
            # cache entry was deleted (manual delete OR auto-rotation above).
            # A real flyer (githubusercontent etc.) is never touched.
            stale_unsplash = ("images.unsplash.com" in fu) and (title_key not in ucache)
            if fu and not stale_unsplash:
                continue
            if searched >= SEARCH_CAP and title_key not in ucache:
                # hit the safety cap — leave this one for the next run
                continue
            before = len(ucache)
            url = unsplash_image_for(ev.get("title", ""), ucache)
            if len(ucache) > before:
                searched += 1
            if url:
                ev["flyer_url"] = url
                ev["_img_source"] = "unsplash"
        if portrait_fixed:
            print(f"  🖼  Portrait fix: updated {portrait_fixed} event(s) from landscape → portrait URL")
        _save_unsplash_cache(ucache)
        # don't count the bookkeeping key in the "cached total"
        total_cached = len([k for k in ucache if not k.startswith("__")])
        print(f"  🖼  Unsplash themed images: {searched} new searches this run "
              f"({total_cached} cached total)")

    # SAFETY: warn loudly if this run drastically shrinks the directory — a sign something
    # went wrong (bad scrape, over-merge). The data is still written, but you'll see the alert.
    if len(existing) >= 10 and len(merged) < len(existing) * 0.5:
        print(f"  ⚠️  WARNING: directory dropped from {len(existing)} to {len(merged)} events. "
              f"If unexpected, check the funnel above — a source may have returned little this run.")

    out = {"generated_at": dt.datetime.utcnow().isoformat() + "Z", "events": merged}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  (carried forward {carried} still-upcoming events from previous runs)")

    print("\n" + "="*60)
    print("FUNNEL (where things drop off):")
    print(f"  posts scraped ........ {len(posts)}")
    print(f"  extraction failures .. {_extract_fail_count[0]}  (if this ≈ posts scraped, your ANTHROPIC_API_KEY is the problem)")
    print(f"  judged real events ... {n_is_event}")
    print(f"  still upcoming ....... {n_upcoming}")
    print(f"  website feeds ........ {n_wp}  (intl centres: Nisarga, Osho World, San Diego…)")
    print(f"  flyer uploads ........ {n_flyer}  (from your '{FLYERS_DIR}/' folder)")
    print(f"  new this run ......... {len(events)}")
    print(f"  TOTAL in directory ... {len(merged)}  (new + carried forward)")
    print("="*60)
    print(f"✓ events.json now holds {len(merged)} upcoming events")


if __name__ == "__main__":
    build()
