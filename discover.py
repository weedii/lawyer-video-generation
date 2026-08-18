"""STAGE 1 of the automation: find the best stories, before we spend a cent making video.

What it does, in order:
  1. FIND  — pull the recent story links off the RollOnFriday front page (no pasting).
  2. SKIP  — drop any link we've already scored on a previous run (kept in queue/history.json),
             so every run only looks at genuinely new stories.
  3. READ  — scrape each new story's full text (reusing scrape.py).
  4. SCORE — GPT-4.1 rates each story on three axes (drama / juicy / lawyer-specific); the
             overall score is combined from those in code, so it's deterministic, not a vibe.
  5. PICK  — a MIN/MAX hybrid: keep every story at or above the quality bar, but never fewer
             than MIN and never more than MAX, and never pick a story below the FLOOR even to
             reach MIN (so a weak week is honestly reported, not padded with junk).
  6. SAVE  — the picks are appended to queue/story_queue.json (the list STAGE 2 will make).

Stage 1 makes NO video and NO images — it only reads and scores, so it's cheap: one GPT-4.1
call per new story (pennies). It prints the exact cost at the end like every other script.

Usage:
    python discover.py            # find + score + pick, using the defaults below
    python discover.py --rescore  # ignore history and score every candidate again (testing)
    python discover.py --show     # just print the current queue + history, score nothing (free)

Reads:  the RollOnFriday front page (live)
Writes: queue/history.json      (every link ever scored, so we never re-score or re-pick one)
        queue/story_queue.json  (the picked stories waiting for Stage 2 to turn into videos)

The queue/ folder lives OUTSIDE output/ on purpose: output/ gets wiped for each video, but the
discovery memory has to survive across every video so we don't keep re-surfacing the same story.
"""
import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urldefrag
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
import costs
import scrape   # reuse the exact scraper the video pipeline already uses (scrape.scrape / HEADERS)

load_dotenv()

KEY = os.getenv("OPENAI_API_KEY")
if not KEY:
    sys.exit("ERROR: OPENAI_API_KEY is empty. Paste your OpenAI key in .env.")

# Same text model the rest of the pipeline trusts to judge a legal story (analyze.py). The
# cheap mini was too weak elsewhere, so we keep one model for consistent judgement. Pennies here.
MODEL = "gpt-4.1"

# --- Where to look -----------------------------------------------------------------------
# The RollOnFriday news list. The homepage and /latest-news both return ~21 recent story
# links; /news and /news-content are 404s, so they're not used. We try these in order and
# take the first page that yields enough links, so one URL changing doesn't break discovery.
LISTING_URLS = [
    "https://www.rollonfriday.com/latest-news",
    "https://www.rollonfriday.com/",
]
# Only real articles live under /news-content/<slug>; everything else on the page (sections,
# adverts, the forum) is ignored by matching this path.
STORY_PATH = "/news-content/"

# --- The knobs you can turn (env vars override, so you never have to edit this file) -------
# How many recent stories to pull and score each run (the candidate pool). ~10 = the front page.
CANDIDATE_COUNT = int(os.getenv("DISCOVER_CANDIDATES", "10"))
# The MIN/MAX hybrid, on a 0-10 overall score:
BAR = float(os.getenv("DISCOVER_BAR", "7"))       # at/above this = a clear pick
FLOOR = float(os.getenv("DISCOVER_FLOOR", "5"))   # never pick below this, even to reach MIN
MIN_KEEP = int(os.getenv("DISCOVER_MIN", "2"))    # never keep fewer than this (if that many clear FLOOR)
MAX_KEEP = int(os.getenv("DISCOVER_MAX", "5"))    # never keep more than this
# How many stories to scrape+score at once. They're independent (each its own network calls),
# so we run a few in parallel; set to 1 to go fully serial if a site ever rate-limits.
WORKERS = int(os.getenv("DISCOVER_WORKERS", "6"))

# A scraped page shorter than this many characters isn't a real story (an empty/broken page or
# a one-line housekeeping post). We score it 0 WITHOUT calling GPT, so we don't pay to judge junk.
MIN_BODY_CHARS = 250

# The overall score is a fixed weighted blend of the three axes, computed HERE (not by the
# model) so the same sub-scores always give the same overall — auditable and tunable. Drama
# carries the most weight (a TikTok microdrama lives or dies on conflict), then how juicy/
# shareable it is, then how lawyer-specific it is (the niche filter, but every RollOnFriday
# story is already legal, so it's the smallest weight).
WEIGHTS = {"drama": 0.40, "juicy": 0.35, "lawyer_specific": 0.25}

QUEUE_DIR = "queue"
HISTORY = os.path.join(QUEUE_DIR, "history.json")
STORY_QUEUE = os.path.join(QUEUE_DIR, "story_queue.json")

SCORE_PROMPT = """You pick real legal-gossip stories to turn into TikTok-style microdramas for
young, high-paying lawyers. A good story is a mini-drama: conflict, a twist, a downfall, a
scandal, a "you won't believe what this lawyer did". A bad story is housekeeping (a holiday
notice, a survey, a pay-table roundup) or dry news with no human drama.

Rate THIS story on three axes, each 0-10:
- drama: is there real conflict or a story arc — a rise/fall, a clash, a shocking act, a twist?
- juicy: is it gossipy, surprising, shareable, emotionally charged — would a lawyer send it to a friend?
- lawyer_specific: is it truly about lawyers / law firms / the legal world (insider), not generic news?

Also decide is_story: true if it's an actual news story, false if it's a promo, holiday note,
survey, advert or housekeeping post.

Return ONLY JSON:
{
  "is_story": true/false,
  "drama": 0-10,
  "juicy": 0-10,
  "lawyer_specific": 0-10,
  "verdict": "one short sentence: why this would or wouldn't make a good microdrama",
  "hook": "one punchy first-person line that could open the video (empty string if not a story)"
}
"""


# --------------------------------------------------------------------------- find links

def fetch_candidate_links(limit: int) -> list:
    """Pull recent story links off the RollOnFriday front page. Tries each listing URL until one
    yields enough links, so a single page breaking doesn't stop discovery. Strips the #comments
    fragment and de-dupes while keeping the page's order (newest first), then returns the top N."""
    for base in LISTING_URLS:
        try:
            r = requests.get(base, headers=scrape.HEADERS, timeout=30)
            if r.status_code != 200:
                print(f"  listing {base} returned HTTP {r.status_code}, trying the next one ...")
                continue
        except Exception as e:
            print(f"  listing {base} failed ({type(e).__name__}), trying the next one ...")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        links, seen = [], set()
        for a in soup.find_all("a", href=True):
            if STORY_PATH not in a["href"]:
                continue
            # urldefrag drops "#comments" so the article and its comments link count as one.
            full = urldefrag(urljoin(base, a["href"]))[0]
            if full in seen:
                continue
            seen.add(full)
            links.append(full)
        if len(links) >= MIN_KEEP:      # this page gave us a usable list
            print(f"  found {len(links)} story links on {base}; taking the newest {min(limit, len(links))}.")
            return links[:limit]
    print("  WARNING: no story links found on any listing page.")
    return []


# --------------------------------------------------------------------------- memory (queue/)

def _load(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def load_history() -> dict:
    """Every link we've ever scored, keyed by url -> its record. Used to skip re-scoring."""
    return _load(HISTORY, {})


def load_queue() -> list:
    """The stories already picked and waiting for Stage 2 (list of records)."""
    return _load(STORY_QUEUE, [])


def _save(path: str, data):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- score one story

def overall_score(sub: dict) -> float:
    """Blend the three 0-10 axes into one overall score with the fixed WEIGHTS (done in code so
    identical sub-scores always give an identical overall). Rounded to one decimal for display."""
    return round(sum(sub.get(k, 0) * w for k, w in WEIGHTS.items()), 1)


def score_story(client: OpenAI, story: dict) -> tuple:
    """Send one story to GPT-4.1 and get back the three axis scores + a verdict + a hook.
    Returns (record_without_url, cost). A page too short to be a real story is scored 0 here
    without spending a GPT call."""
    body = story.get("body", "") or ""
    if len(body) < MIN_BODY_CHARS:
        rec = {"title": story.get("title", ""), "is_story": False,
               "drama": 0, "juicy": 0, "lawyer_specific": 0, "overall": 0.0,
               "verdict": "Too short to be a real story (likely a promo or broken page).",
               "hook": ""}
        return rec, 0.0

    # Full title + body is the "full story text" scoring the user chose. Comments are left out:
    # they're long and don't change whether the STORY itself is dramatic, so including them would
    # just add tokens (cost) for no better judgement.
    content = f"TITLE: {story['title']}\n\nSTORY:\n{body}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SCORE_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        temperature=0,     # deterministic scoring: the same story scores the same every run
    )
    cost = costs.openai_cost(resp.usage.prompt_tokens, resp.usage.completion_tokens)
    j = json.loads(resp.choices[0].message.content)
    sub = {k: float(j.get(k, 0) or 0) for k in WEIGHTS}
    rec = {
        "title": story.get("title", ""),
        "is_story": bool(j.get("is_story", True)),
        **{k: sub[k] for k in WEIGHTS},
        # A non-story can still score high on the axes, so force its overall to 0 — we never
        # want a "great holiday notice" outranking a real scandal.
        "overall": overall_score(sub) if j.get("is_story", True) else 0.0,
        "verdict": (j.get("verdict") or "").strip(),
        "hook": (j.get("hook") or "").strip(),
    }
    return rec, cost


def evaluate(url: str) -> dict:
    """Scrape + score ONE candidate. Runs in a worker thread (each touches only its own url and
    its own network calls), so a batch scores in parallel. Wrapped so one bad link just skips."""
    client = OpenAI(api_key=KEY, timeout=45.0, max_retries=3)
    try:
        story = scrape.scrape(url)
        rec, cost = score_story(client, story)
        rec["url"] = url
        rec["scored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["cost"] = round(cost, 6)
        return rec
    except Exception as e:
        print(f"  skipped {url} ({type(e).__name__}: {e})")
        return None


# --------------------------------------------------------------------------- pick the best

def select(records: list) -> list:
    """The MIN/MAX hybrid. records must be sorted best-first. Keep every story at/above the BAR,
    capped at MAX_KEEP; if fewer than MIN_KEEP clear the BAR, top up with the next-best stories
    that still clear the FLOOR (never below it) until we reach MIN_KEEP or run out of decent ones."""
    picks = [r for r in records if r["overall"] >= BAR]
    if len(picks) > MAX_KEEP:
        return picks[:MAX_KEEP]
    if len(picks) < MIN_KEEP:
        for r in records:
            if len(picks) >= MIN_KEEP:
                break
            if r in picks:
                continue
            if r["overall"] >= FLOOR:      # decent enough to make, just below the "clear pick" bar
                picks.append(r)
    return picks


# --------------------------------------------------------------------------- output

def print_table(records: list, picks: list):
    """Show every scored story best-first, with a PICK / -- marker, the axis scores and the verdict."""
    picked_urls = {r["url"] for r in picks}
    print("\n" + "=" * 78)
    print("  STORIES SCORED THIS RUN  (best first)")
    print("=" * 78)
    if not records:
        print("  (no new stories to score)")
    for r in records:
        mark = "PICK" if r["url"] in picked_urls else " -- "
        print(f"  [{mark}] {r['overall']:>4.1f}/10  "
              f"(drama {r['drama']:.0f}, juicy {r['juicy']:.0f}, lawyer {r['lawyer_specific']:.0f})  "
              f"{r['title'][:52]}")
        if r.get("verdict"):
            print(f"          {r['verdict'][:72]}")
    print("=" * 78)


def show_current(history: dict, queue: list):
    """--show mode: print what's already known (the pending queue + how many links we've scored),
    without touching the network or spending anything."""
    print("\nPENDING QUEUE (picked, waiting for Stage 2):")
    if not queue:
        print("  (empty)")
    for r in sorted(queue, key=lambda x: x.get("overall", 0), reverse=True):
        print(f"  {r.get('overall', 0):>4.1f}/10  {r.get('title','')[:60]}")
        print(f"          {r.get('url','')}")
    print(f"\nHISTORY: {len(history)} link(s) scored so far (these are skipped on future runs).")


# --------------------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    history = load_history()
    queue = load_queue()

    if "--show" in args:
        show_current(history, queue)
        return

    rescore = "--rescore" in args      # testing: ignore history and score everything again

    print("Looking for recent stories on RollOnFriday ...")
    candidates = fetch_candidate_links(CANDIDATE_COUNT)
    if not candidates:
        sys.exit("No candidate stories found — the site layout may have changed. Nothing scored.")

    # Skip anything already scored on a past run, so each run only spends on genuinely new stories.
    todo = candidates if rescore else [u for u in candidates if u not in history]
    skipped = len(candidates) - len(todo)
    if skipped:
        print(f"  skipping {skipped} link(s) already scored on a previous run.")
    if not todo:
        print("\nNothing new since the last run — no stories to score. (Cost: $0.00)")
        show_current(history, queue)
        return

    print(f"\nScoring {len(todo)} new story(ies) with {MODEL} "
          f"({min(WORKERS, len(todo))} at a time) ...")
    records = []
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
        for fut in as_completed([pool.submit(evaluate, u) for u in todo]):
            r = fut.result()
            if r:
                records.append(r)

    # Best first. Record EVERY scored story into history (picked or not) so we never re-score it.
    records.sort(key=lambda r: r["overall"], reverse=True)
    for r in records:
        history[r["url"]] = r
    _save(HISTORY, history)

    picks = select(records)

    # Append the new picks to the pending queue (Stage 2 reads this). De-dupe by url so running
    # Stage 1 twice can't queue the same story twice; keep the queue best-first.
    have = {r.get("url") for r in queue}
    for r in picks:
        if r["url"] not in have:
            entry = dict(r)
            entry["status"] = "pending"      # Stage 2 will flip this to "made"
            queue.append(entry)
    queue.sort(key=lambda r: r.get("overall", 0), reverse=True)
    _save(STORY_QUEUE, queue)

    print_table(records, picks)

    total_cost = sum(r.get("cost", 0.0) for r in records)
    print(f"\nPicked {len(picks)} story(ies); the pending queue now holds {len(queue)}.")
    print(f"Saved -> {STORY_QUEUE}")
    if picks:
        print("\nNext up for Stage 2 (highest score first):")
        for r in picks:
            print(f"  {r['overall']:>4.1f}/10  {r['title'][:60]}")
    costs.show(f"Scored {len(records)} story(ies) - OpenAI {MODEL}", total_cost)


if __name__ == "__main__":
    main()
