#!/usr/bin/env python3
"""
Daily Osho page discovery for oshocamps.com
Searches Google (Programmable Search, restricted to facebook.com + instagram.com)
for "osho meditation <district>", rotating through all districts of India.
New, never-seen pages -> Claude relevance filter -> candidates.json for manual review.
No external pip packages needed (urllib only).
"""
import json, os, re, sys, time, urllib.parse, urllib.request

CSE_KEY   = os.environ.get("GOOGLE_CSE_KEY", "")
CSE_ID    = os.environ.get("GOOGLE_CSE_ID", "")
CLAUDE_KEY= os.environ.get("ANTHROPIC_API_KEY", "")
BATCH     = int(os.environ.get("DISCOVER_BATCH", "80"))   # districts per day (<=95 to stay in free quota)
MIN_SCORE = int(os.environ.get("DISCOVER_MIN_SCORE", "4"))

if not CSE_KEY or not CSE_ID:
    sys.exit("Missing GOOGLE_CSE_KEY / GOOGLE_CSE_ID secrets")

def load(path, fallback):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return fallback

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

# ---------------- URL normalization ----------------
FB_SKIP = {"watch","reel","reels","login","sharer","share","story.php","hashtag","marketplace",
           "help","policies","events","video.php","photo.php","photo","photos","permalink.php",
           "search","public","directory","places","media","gaming","live","dialog","plugins",
           "l.php","privacy","legal","business","about","careers","settings","notes","ads"}
IG_SKIP = {"p","reel","reels","explore","stories","accounts","tv","directory","about","legal",
           "web","developer","static"}

def normalize(url):
    """Return (platform, canonical_page_url) or None."""
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return None
    host = u.netloc.lower()
    for m in ("m.", "web.", "mbasic.", "www."): host = host.removeprefix(m)
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("facebook.com"):
        if not parts: return None
        p0 = parts[0].lower()
        if p0 in FB_SKIP: return None
        if p0 == "profile.php":
            pid = urllib.parse.parse_qs(u.query).get("id", [None])[0]
            return ("facebook", f"https://www.facebook.com/profile.php?id={pid}") if pid else None
        if p0 == "people" and len(parts) >= 3:
            return ("facebook", f"https://www.facebook.com/people/{parts[1]}/{parts[2]}")
        if p0 == "groups" and len(parts) >= 2:
            return ("facebook", f"https://www.facebook.com/groups/{parts[1]}")
        if p0 == "pages" and len(parts) >= 3:
            return ("facebook", f"https://www.facebook.com/pages/{parts[1]}/{parts[2]}")
        return ("facebook", f"https://www.facebook.com/{parts[0]}")
    if host.endswith("instagram.com"):
        if not parts or parts[0].lower() in IG_SKIP: return None
        return ("instagram", f"https://www.instagram.com/{parts[0]}/")
    return None

def key(url):  # dedupe key
    return url.lower().rstrip("/")

# ---------------- Google CSE ----------------
def cse_search(query):
    qs = urllib.parse.urlencode({"key": CSE_KEY, "cx": CSE_ID, "q": query, "num": 10, "gl": "in"})
    req = urllib.request.Request(f"https://www.googleapis.com/customsearch/v1?{qs}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("items", [])
    except urllib.error.HTTPError as e:
        # Surface Google's real reason on the first failure for diagnosis
        try:
            detail = json.load(e).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        print(f"  Google API {e.code}: {detail[:200]}")
        raise

# ---------------- Claude relevance filter ----------------
def claude_filter(items):
    """items: [{url,title,snippet,district}] -> add score+reason. Fail-open on any error."""
    if not CLAUDE_KEY or not items:
        for it in items: it["score"] = 5; it["reason"] = "unfiltered"
        return items
    out = []
    for i in range(0, len(items), 40):
        chunk = items[i:i+40]
        listing = json.dumps([{"url": c["url"], "title": c.get("title",""),
                               "snippet": c.get("snippet",""), "district": c["district"]} for c in chunk],
                             ensure_ascii=False)
        prompt = ("You are filtering search results for a directory of Osho meditation camps in India. "
                  "For each result below (a Facebook/Instagram page, group or profile), score 0-10 the likelihood "
                  "that this account POSTS Osho meditation camps/events/shivirs (centres, ashrams, sannyas communities, "
                  "camp organisers). Score LOW for: pure quote/wallpaper pages, book sellers, unrelated namesakes, "
                  "news articles, anti-Osho pages. Respond ONLY with a JSON array: "
                  '[{"url":"...","score":N,"reason":"few words"}] and nothing else.\n\n' + listing)
        body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 3500,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                     headers={"content-type": "application/json",
                                              "x-api-key": CLAUDE_KEY,
                                              "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = json.load(r)["content"][0]["text"]
            txt = re.sub(r"```json|```", "", txt).strip()
            verdicts = {v["url"]: v for v in json.loads(txt)}
            for c in chunk:
                v = verdicts.get(c["url"], {})
                c["score"] = int(v.get("score", 5)); c["reason"] = v.get("reason", "")
        except Exception as e:
            print("Claude filter failed (keeping all):", e)
            for c in chunk: c["score"] = 5; c["reason"] = "filter-error"
        out.extend(chunk)
    return out

# ---------------- Build the 'already seen' set ----------------
def seen_set():
    seen = set()
    for c in load("candidates.json", []): seen.add(key(c["url"]))
    rev = load("reviewed.json", {"approved": [], "rejected": []})
    for lst in rev.values():
        for c in lst: seen.add(key(c["url"]))
    # any FB/IG url anywhere inside sources.json, whatever its structure
    try:
        raw = open("sources.json", encoding="utf-8").read()
        for m in re.findall(r"https?://[^\s\"',\\]+", raw):
            n = normalize(m)
            if n: seen.add(key(n[1]))
    except FileNotFoundError:
        pass
    return seen

# ---------------- Main ----------------
def main():
    districts = load("districts.json", [])
    if not districts: sys.exit("districts.json missing")
    state = load("discover_state.json", {"pointer": 0, "pass": 1})
    candidates = load("candidates.json", [])
    seen = seen_set()
    ptr, n = state["pointer"], len(districts)
    today = time.strftime("%Y-%m-%d")
    fresh, done = [], 0

    for i in range(BATCH):
        d = districts[(ptr + i) % n]
        try:
            results = cse_search(f'osho meditation {d["term"]}')
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"Quota hit after {done} districts — stopping for today.")
                break
            print(f'{d["term"]}: HTTP {e.code}, skipping'); done += 1; continue
        except Exception as e:
            print(f'{d["term"]}: {e}, skipping'); done += 1; continue
        done += 1
        for item in results:
            norm = normalize(item.get("link", ""))
            if not norm: continue
            platform, url = norm
            if key(url) in seen: continue
            seen.add(key(url))
            fresh.append({"url": url, "platform": platform,
                          "title": re.sub(r"\s*\|\s*Facebook$|\s*•\s*Instagram.*$", "", item.get("title", "")),
                          "snippet": (item.get("snippet") or "")[:240],
                          "district": d["term"], "state": d["state"], "found": today})
        time.sleep(0.4)

    if (ptr + done) >= n and done:
        state["pass"] = state.get("pass", 1) + 1
        print(f'Full India pass #{state["pass"]-1} complete!')
    state["pointer"] = (ptr + done) % n
    state["last_run"] = today
    state["last_batch"] = f'{districts[ptr]["term"]} -> {districts[(ptr+max(done-1,0))%n]["term"]} ({done} districts)'

    fresh = [c for c in claude_filter(fresh) if c.get("score", 5) >= MIN_SCORE]
    candidates.extend(fresh)
    save("candidates.json", candidates)
    save("discover_state.json", state)
    print(f"Searched {done} districts | {len(fresh)} new candidates | queue now {len(candidates)}")

if __name__ == "__main__":
    main()
