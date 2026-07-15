#!/usr/bin/env python3
"""
Daily Osho page discovery for oshocamps.com  (v2 — dual engine)
Engine 1: Tavily Search API (free 1000/month, no card) — set TAVILY_API_KEY secret
Engine 2: DuckDuckGo HTML search — free, no key (automatic fallback)
(Google CSE removed: closed to new customers, discontinued Jan 2027)
Rotates through all districts of India; new pages -> Claude filter -> candidates.json.
No pip packages needed (urllib only).
"""
import html as htmllib
import json, os, re, sys, time, urllib.parse, urllib.request

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TAVILY_DAILY = int(os.environ.get("TAVILY_DAILY", "30"))   # ~30/day = 1000/month free tier
MIN_SCORE  = int(os.environ.get("DISCOVER_MIN_SCORE", "4"))

ENGINE = "tavily" if TAVILY_KEY else "ddg"

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

def key(url):
    return url.lower().rstrip("/")

# ---------------- Engine 1: Tavily (free 1000/mo, no card) ----------------
def tavily_search(query):
    body = json.dumps({"query": query, "max_results": 10,
                       "include_domains": ["facebook.com", "instagram.com"]}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {TAVILY_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [{"link": it.get("url", ""), "title": it.get("title", ""),
             "snippet": (it.get("content") or "")[:240]} for it in data.get("results", [])]

# ---------------- Engine 2: DuckDuckGo (free, no key) ----------------
UA = ("Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")

def ddg_search(query):
    data = urllib.parse.urlencode({"q": query, "kl": "in-en"}).encode()
    req = urllib.request.Request("https://html.duckduckgo.com/html/", data=data,
                                 headers={"User-Agent": UA,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "ignore")
    items, seen_local = [], set()
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if "uddg=" in href:  # DDG redirect wrapper -> real URL
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            href = q.get("uddg", [""])[0]
        href = urllib.parse.unquote(href)
        if not href.startswith("http") or href in seen_local: continue
        seen_local.add(href)
        items.append({"link": href, "title": htmllib.unescape(title), "snippet": ""})
    return items

# ---------------- Regional-language queries ----------------
# Websites belong to famous centres. Rural camps announce in the local language or not at
# all — so an English-only query is structurally blind to most of India's camps.
#
# COST NOTE: this adds NO queries. Each district still gets exactly ONE search; we just ask
# in the right language for that state, and alternate regional/English on each full India
# pass (discover_state.json "pass"). Tavily budget and pass speed are unchanged.

STATE_LANG = {
    # Hindi belt — the biggest camp region (Osho's own MP, plus CG/RJ/UP/BR/HR/DL/UK/JH/HP)
    "Madhya Pradesh": "hi", "Chhattisgarh": "hi", "Rajasthan": "hi", "Uttar Pradesh": "hi",
    "Bihar": "hi", "Haryana": "hi", "Delhi": "hi", "NCT of Delhi": "hi", "Uttarakhand": "hi",
    "Jharkhand": "hi", "Himachal Pradesh": "hi",
    # Gujarati — Osho's oldest base after MP
    "Gujarat": "gu", "Dadra and Nagar Haveli and Daman and Diu": "gu",
    # Marathi (Devanagari script, but "शिबिर" not "शिविर")
    "Maharashtra": "mr", "Goa": "mr",
    # Punjabi
    "Punjab": "pa", "Chandigarh": "pa",
    # Malayalam — Osho Lovers Kerala run camps across Kerala's small towns
    "Kerala": "ml", "Lakshadweep": "ml",
}

# One natural phrase per language — what an organiser would actually write.
LANG_QUERY = {
    "hi": "ओशो ध्यान शिविर",
    "mr": "ओशो ध्यान शिबिर",
    "gu": "ઓશો ધ્યાન શિબિર",
    "pa": "ਓਸ਼ੋ ਧਿਆਨ ਸ਼ਿਵਿਰ",
    "ml": "ഓഷോ ധ്യാന ശിബിരം",
    "en": "osho meditation",
}

def query_for(district, state, use_regional):
    """Build this district's single search query. Falls back to English where we have no
    mapping (Bengal, Tamil Nadu, the North-East…) — Osho presence there is thin enough that
    a dedicated script isn't worth a query slot yet. Add to STATE_LANG if that changes."""
    lang = STATE_LANG.get((state or "").strip(), "en")
    if not use_regional or lang == "en":
        return f"osho meditation {district}"
    return f"{LANG_QUERY[lang]} {district}"


def search_district(term, query=None):
    """Return list of raw results for a district, via active engine."""
    global ENGINE
    q = query or f"osho meditation {term}"
    if ENGINE == "tavily":
        try:
            return tavily_search(q)
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode()[:120]
            except Exception: pass
            print(f"  tavily {e.code}: {body}")
            if e.code in (401, 403, 432, 429):   # bad key / out of credits
                print(">>> Tavily unavailable — switching to DuckDuckGo.")
                ENGINE = "ddg"
            else:
                return []
        except Exception as e:
            print(f"  tavily error: {e}")
            return []
    if ENGINE == "ddg":
        out = []
        for site in ("facebook.com", "instagram.com"):
            try:
                out += ddg_search(f"{q} site:{site}")
            except Exception as e:
                print(f"  ddg {term}/{site}: {e}")
            time.sleep(6)  # slow and steady survives throttling longer
        return out
    return []

# ---------------- Claude relevance filter ----------------
def claude_filter(items):
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

# ---------------- Already-seen set ----------------
def seen_set():
    seen = set()
    for c in load("candidates.json", []): seen.add(key(c["url"]))
    rev = load("reviewed.json", {"approved": [], "rejected": []})
    for lst in rev.values():
        for c in lst: seen.add(key(c["url"]))
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
    global ENGINE
    districts = load("districts.json", [])
    if not districts: sys.exit("districts.json missing")
    state = load("discover_state.json", {"pointer": 0, "pass": 1})
    candidates = load("candidates.json", [])
    seen = seen_set()
    ptr, n = state["pointer"], len(districts)
    today = time.strftime("%Y-%m-%d")
    fresh, done = [], 0

    # Alternate language on each full India pass: odd pass = English, even pass = regional.
    # Costs nothing extra and means every district eventually gets asked both ways.
    # Override any time with:  DISCOVER_LANG=regional  (or =english) in the workflow env.
    _force = os.environ.get("DISCOVER_LANG", "").strip().lower()
    if _force in ("regional", "hi", "local"):
        use_regional = True
    elif _force in ("english", "en"):
        use_regional = False
    else:
        use_regional = (state.get("pass", 1) % 2 == 0)

    print(f"Engine: {ENGINE} | starting at district #{ptr} | pass {state.get('pass',1)} "
          f"| asking in: {'REGIONAL language per state' if use_regional else 'English'}")
    tav_used, ddg_used = 0, 0
    for i in range(700):
        if ENGINE == "tavily" and tav_used >= TAVILY_DAILY:
            print(f"Tavily budget ({TAVILY_DAILY}) done — DuckDuckGo bonus round.")
            ENGINE = "ddg"
        if ENGINE == "ddg" and ddg_used >= 20:   # bonus round cap
            break
        d = districts[(ptr + i) % n]
        eng = ENGINE
        results = search_district(d["term"], query_for(d["term"], d.get("state"), use_regional))
        if eng == "tavily": tav_used += 1
        else: ddg_used += 1
        done += 1
        for item in results:
            norm = normalize(item.get("link", ""))
            if not norm: continue
            platform, url = norm
            if key(url) in seen: continue
            seen.add(key(url))
            fresh.append({"url": url, "platform": platform,
                          "title": re.sub(r"\s*\|\s*Facebook$|\s*[•|-]\s*Instagram.*$", "",
                                          item.get("title", "")).strip(),
                          "snippet": (item.get("snippet") or "")[:240],
                          "district": d["term"], "state": d["state"], "found": today})
        if ENGINE == "tavily":
            time.sleep(1.0)

    if (ptr + done) >= n and done:
        state["pass"] = state.get("pass", 1) + 1
        print(f'Full India pass complete!')
    state["pointer"] = (ptr + done) % n
    state["last_run"] = today
    state["engine"] = ENGINE
    state["lang_mode"] = "regional" if use_regional else "english"
    state["last_batch"] = f'{districts[ptr]["term"]} -> {districts[(ptr+max(done-1,0))%n]["term"]} ({done} districts, {ENGINE})'

    fresh = [c for c in claude_filter(fresh) if c.get("score", 5) >= MIN_SCORE]
    candidates.extend(fresh)
    save("candidates.json", candidates)
    save("discover_state.json", state)
    print(f"Searched {done} districts via {ENGINE} | {len(fresh)} new candidates | queue now {len(candidates)}")

if __name__ == "__main__":
    main()
