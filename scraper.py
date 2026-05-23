#!/usr/bin/env python3
"""
Sannyas Gatherings — hourly scraper pipeline
=============================================
Flow:
  1. Apify scrapes Instagram + Facebook for Osho event posts (hashtags + search).
  2. Claude (Haiku) reads each post and extracts structured event fields.
  3. We dedupe, map country -> region, drop past events, write events.json.

Run locally:   APIFY_TOKEN=... ANTHROPIC_API_KEY=... python scraper.py
Run hourly:    see refresh.yml (GitHub Actions cron)

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
FB_ACTOR = "apify~facebook-posts-scraper"

# What we look for. Add/remove freely.
IG_HASHTAGS = ["oshomeditation", "oshocamp", "oshoretreat", "dynamicmeditation",
               "kundalinimeditation", "oshogathering", "sannyas", "oshofestival"]
SEARCH_TERMS = ["osho meditation camp", "osho retreat", "osho meditation workshop",
                "osho gathering", "dynamic meditation camp", "mystic rose meditation"]

POSTS_PER_QUERY = 30          # tune for cost; each post = a little Apify + Claude spend
MAX_POST_AGE_DAYS = 120       # ignore posts older than this (they rarely announce future camps)

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
    """Run an Apify actor synchronously and return its dataset items."""
    url = APIFY_BASE.format(actor=actor)
    try:
        r = requests.post(url, json=payload, timeout=600)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ! actor {actor} failed: {e}")
        return []

def scrape_instagram():
    print("Scraping Instagram…")
    payload = {
        "search": " ".join(SEARCH_TERMS[:3]),   # search query
        "searchType": "hashtag",
        "hashtags": IG_HASHTAGS,
        "resultsType": "posts",
        "resultsLimit": POSTS_PER_QUERY,
        "addParentData": False,
    }
    items = run_actor(IG_ACTOR, payload)
    posts = []
    for it in items:
        cap = it.get("caption") or it.get("text") or ""
        if not cap:
            continue
        posts.append({
            "caption": cap,
            "url": it.get("url") or it.get("postUrl") or "",
            "image": it.get("displayUrl") or (it.get("images") or [None])[0],
            "platform": "Instagram",
            "timestamp": it.get("timestamp") or it.get("takenAt"),
        })
    print(f"  → {len(posts)} Instagram posts")
    return posts

def scrape_facebook():
    print("Scraping Facebook…")
    # Facebook actors usually take page URLs or search terms; adjust input to your chosen actor.
    payload = {
        "searchQueries": SEARCH_TERMS,
        "resultsLimit": POSTS_PER_QUERY,
    }
    items = run_actor(FB_ACTOR, payload)
    posts = []
    for it in items:
        cap = it.get("text") or it.get("message") or ""
        if not cap:
            continue
        posts.append({
            "caption": cap,
            "url": it.get("url") or it.get("postUrl") or "",
            "image": (it.get("media") or [{}])[0].get("photo_image", {}).get("uri")
                     if it.get("media") else it.get("imageUrl"),
            "platform": "Facebook",
            "timestamp": it.get("time") or it.get("timestamp"),
        })
    print(f"  → {len(posts)} Facebook posts")
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
    "state is the Indian state when country is India, else null. Keep description under 30 words."
)

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
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"  ! extraction failed: {e}")
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
    posts = scrape_instagram() + scrape_facebook()
    print(f"\nExtracting {len(posts)} posts with Claude…")

    events, seen = [], set()
    for i, p in enumerate(posts, 1):
        ev = extract_event(p["caption"])
        if not ev.get("is_event"):
            continue
        ev["source_url"] = p["url"]
        ev["source_platform"] = p["platform"]
        ev["flyer_url"] = p.get("image") or ""
        ev["country"] = ev.get("country") or "India"
        ev["region"] = REGION_MAP.get(ev["country"], "Asia")
        if not keep_upcoming(ev):
            continue
        ev["id"] = make_id(ev)
        if ev["id"] in seen:                       # dedupe
            continue
        seen.add(ev["id"])
        events.append(ev)
        if i % 10 == 0:
            print(f"  …processed {i}/{len(posts)}")

    events.sort(key=lambda e: e.get("start_date") or "9999")
    out = {"generated_at": dt.datetime.utcnow().isoformat() + "Z", "events": events}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Wrote {len(events)} upcoming events to {OUTPUT_FILE}")


if __name__ == "__main__":
    build()
