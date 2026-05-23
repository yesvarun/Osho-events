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
import requests

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
APIFY_TOKEN     = os.environ["APIFY_TOKEN"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
EXTRACT_MODEL   = "claude-haiku-4-5-20251001"   # cheap + fast for per-post extraction
OUTPUT_FILE     = "events.json"

# Apify actors (creator~actor-name). Swap if you prefer a different actor.
IG_ACTOR = "apify~instagram-scraper"
FB_PAGES_ACTOR  = "apify~facebook-posts-scraper"            # scrapes specific page URLs (reliable)
FB_GROUPS_ACTOR = "apify~facebook-groups-scraper"           # scrapes public group posts

# Known, active Osho Facebook PAGES. Temporarily EMPTY to save Apify credit
# (Facebook is the pricier source). Re-add these URLs when you have paid credit:
#   "https://www.facebook.com/osho.international.meditation.resort/",
#   "https://www.facebook.com/OSHOInternational/",
#   "https://www.facebook.com/oshoworld/",
#   "https://www.facebook.com/oshonisarga/",
FB_PAGES = []

# Public Osho Facebook GROUPS to scrape. Groups are where many regional camps get
# announced. Paste public group URLs here, e.g. "https://www.facebook.com/groups/123456/".
# NOTE: PRIVATE groups need your own login cookies — public groups work without login.
FB_GROUPS = [
    # "https://www.facebook.com/groups/oshomeditation/",
    # "https://www.facebook.com/groups/oshosannyas/",
]

# What we look for. Trimmed to the most productive tags to save credit.
IG_HASHTAGS = ["oshomeditation", "oshocamp", "oshoretreat", "oshointernational",
               # Hindi / Punjabi / regional tags to catch regional-language posts:
               "ओशो", "ओशोध्यान", "ध्यानशिविर", "ओशोशिविर", "साधनाशिविर",
               "ਓਸ਼ੋ", "ਧਿਆਨ"]
SEARCH_TERMS = ["osho meditation camp", "osho retreat", "osho meditation workshop",
                "osho gathering", "dynamic meditation camp", "mystic rose meditation",
                "ओशो ध्यान शिविर", "ध्यान शिविर", "ओशो साधना शिविर"]

# IMPORTANT: Instagram now blocks most ANONYMOUS hashtag browsing, which is the #1 reason
# a hashtag scrape returns 0 posts. If your Apify test confirms this, paste a logged-in
# Instagram session cookie below (or set it as a GitHub secret IG_SESSION_COOKIE).
# How to get it: log into instagram.com in a browser → DevTools → Application →
# Cookies → copy the value of the "sessionid" cookie. Format: "sessionid=XXXX…"
IG_SESSION_COOKIE = os.environ.get("IG_SESSION_COOKIE", "")

POSTS_PER_QUERY = 10          # LOW to stay cheap on the free tier (raise later if you add credit)
MAX_POST_AGE_DAYS = 30        # posts from the last 30 days (camps are often announced weeks ahead)

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

def scrape_instagram():
    print("Scraping Instagram…")
    payload = {
        # The official actor scrapes posts from hashtag PAGE urls in one run:
        "directUrls": [f"https://www.instagram.com/explore/tags/{h}/" for h in IG_HASHTAGS],
        "resultsType": "posts",
        "resultsLimit": POSTS_PER_QUERY,
        "onlyPostsNewerThan": f"{MAX_POST_AGE_DAYS} days",
        "addParentData": False,
    }
    # Logged-in cookie dramatically improves hashtag results (often required now)
    if IG_SESSION_COOKIE:
        sid = IG_SESSION_COOKIE.split("sessionid=")[-1].strip().strip(";")
        payload["sessionCookies"] = [{"name":"sessionid","value":sid,"domain":".instagram.com"}]
        print("  (using Instagram session cookie)")
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
        if not cap:
            skipped_no_caption += 1
            continue
        # real post permalink — IG actor returns shortCode; build a clean /p/ link as fallback
        link = it.get("url") or it.get("postUrl") or it.get("inputUrl") or ""
        if not link and it.get("shortCode"):
            link = f"https://www.instagram.com/p/{it['shortCode']}/"
        img = it.get("displayUrl") or it.get("imageUrl")
        if not img and isinstance(it.get("images"), list) and it["images"]:
            img = it["images"][0]
        posts.append({
            "caption": cap,
            "url": link,
            "image": img or "",
            "platform": "Instagram",
            "timestamp": it.get("timestamp") or it.get("takenAt"),
        })
    print(f"  → {len(posts)} Instagram posts with captions"
          + (f"  ({skipped_no_caption} skipped — no caption field found)" if skipped_no_caption else ""))
    if skipped_no_caption and not posts:
        print("    !! All items skipped for missing caption. The actor uses a DIFFERENT field name.")
        print("       Look at the 'sample keys' line above to see the real field names.")
    return posts

def _fb_post(it):
    """Normalise one Facebook dataset item into our shape. Handles multiple actor formats."""
    cap = (it.get("text") or it.get("message") or it.get("message_text")
           or it.get("postText") or it.get("content") or "")
    if not cap:
        return None
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
    "Captions may be in Hindi, Punjabi, or other languages — read them, and OUTPUT all fields "
    "in English (translate title, venue, city, description). Recognise regional date formats."
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

def extract_event(caption):
    body = {
        "model": EXTRACT_MODEL,
        "max_tokens": 600,
        "system": EXTRACT_SYSTEM,
        "messages": [{"role": "user", "content": caption[:4000]}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=body, timeout=60)
        if r.status_code != 200:
            _extract_fail_count[0] += 1
            if _extract_fail_count[0] <= 3:   # don't spam — show first few
                print(f"  ! extraction HTTP {r.status_code}: {r.text[:200]}")
            return {"is_event": False}
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:
        _extract_fail_count[0] += 1
        if _extract_fail_count[0] <= 3:
            print(f"  ! extraction failed: {type(e).__name__}: {e}")
        return {"is_event": False}


# ----------------------------------------------------------------------
# 3. CLEAN / FILTER / WRITE
# ----------------------------------------------------------------------
def make_id(ev):
    key = f"{ev.get('title','')}|{ev.get('start_date','')}|{ev.get('city','')}"
    return hashlib.md5(key.lower().encode()).hexdigest()[:12]

def keep_upcoming(ev):
    end = ev.get("end_date") or ev.get("start_date")
    if not end:
        return False
    try:
        return dt.date.fromisoformat(end) >= dt.date.today()
    except ValueError:
        return False

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

    posts = scrape_instagram() + scrape_facebook()
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
    for i, p in enumerate(posts, 1):
        ev = extract_event(p["caption"])
        if not ev.get("is_event"):
            continue
        n_is_event += 1
        ev["source_url"] = p["url"] if p.get("url") else ""
        ev["source_platform"] = p["platform"]
        ev["flyer_url"] = p.get("image") or ""
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
    for ev in events:                       # new finds first (freshest data wins on dupes)
        if ev["id"] not in seen_ids:
            seen_ids.add(ev["id"]); merged.append(ev)
    carried = 0
    for ev in existing:
        eid = ev.get("id") or make_id(ev)
        if eid in seen_ids:
            continue
        if keep_upcoming(ev):               # only carry forward events that haven't passed
            ev["id"] = eid
            seen_ids.add(eid); merged.append(ev); carried += 1

    merged.sort(key=lambda e: e.get("start_date") or "9999")
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
    print(f"  new this run ......... {len(events)}")
    print(f"  TOTAL in directory ... {len(merged)}  (new + carried forward)")
    print("="*60)
    print(f"✓ events.json now holds {len(merged)} upcoming events")


if __name__ == "__main__":
    build()
