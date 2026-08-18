"""STAGE 2 of the automation: turn the picked stories into videos, into a review folder.

Stage 1 (discover.py) fills queue/story_queue.json with the best stories. This step takes the
top few still marked "pending" and builds a full video for each by running the EXISTING
pipeline (run.py "<url>" --fresh) — so there's ONE implementation of the 8 steps, and every
cost/time line you already know prints per video. After each build it moves the finished
output/ into its own named folder under videos/, so the videos never overwrite each other and
you can review them side by side.

How it behaves (all chosen deliberately):
  - CAPPED batch: makes up to BATCH_MAX videos per run (highest score first), not the whole queue.
  - COST GATE: prints how many videos and a rough total first, then waits for you to type yes
    (skip with --yes / -y for unattended runs).
  - Saved to videos/<date>-<slug>/ so batches sort by date.
  - SKIP on failure: if one video fails it's logged, left "pending" in the queue, and the batch
    keeps going.

Usage:
    python batch_maker.py               # make up to BATCH_MAX pending videos (asks to confirm)
    python batch_maker.py --max 1       # just the top pick this run
    python batch_maker.py --yes         # don't ask, just build (for automation)
    python batch_maker.py --dry-run     # show what WOULD be made + the estimate, spend nothing

Reads:  queue/story_queue.json      (the picks from discover.py)
Writes: videos/<date>-<slug>/       (one finished video per story, with its pieces)
        updates each made story's status to "made" (+ its folder) back in the queue.
"""
import os
import sys
import json
import time
import shutil
import subprocess
from urllib.parse import urlparse
import costs

# How many videos one run will make at most (highest score first). Env override so you can
# change the batch size without editing code, matching the rest of the pipeline's knobs.
BATCH_MAX = int(os.getenv("BATCH_MAX", "3"))

# Rough price shown BEFORE building, only so the cost gate can warn you what a batch will cost.
# It is an estimate (the documented ~$2.60/video); the REAL cost is printed per video by run.py
# and summed from each video's analysis.json at the end, so the final number is never a guess.
PER_VIDEO_EST = 2.60

QUEUE_DIR = "queue"
STORY_QUEUE = os.path.join(QUEUE_DIR, "story_queue.json")
VIDEOS_DIR = "videos"        # each finished video gets its own subfolder here
OUT_DIR = "output"           # where run.py builds each video before we move it out
FINAL_NAME = "final_video.mp4"


def load_queue() -> list:
    try:
        with open(STORY_QUEUE) as f:
            return json.load(f)
    except Exception:
        return []


def save_queue(queue: list):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(STORY_QUEUE, "w") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def url_slug(url: str) -> str:
    """The story's own slug from its URL (/news-content/<slug>) — already clean and readable.
    Truncated so the folder name doesn't get absurdly long."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1] or "story"
    return slug[:60]


def real_cost(video_dir: str) -> float:
    """The TRUE cost of one finished video, read from the costs it recorded in its analysis.json
    (summing each step's real price). Returns 0 if it can't be read, so a missing file never
    invents a number."""
    try:
        with open(os.path.join(video_dir, "analysis.json")) as f:
            data = json.load(f)
        return sum(e.get("amount", 0) for e in data.get("costs", {}).values())
    except Exception:
        return 0.0


def build_one(url: str) -> bool:
    """Build ONE video by running the existing pipeline fresh. Returns True only if it finished
    with a real final_video.mp4. --fresh wipes output/ first (no prompt), so each story starts
    clean; we deliberately reuse run.py so there's a single implementation of the 8 steps."""
    # Pass our environment through (so COSTLOG=1 still gives the real reconciled cost per video).
    result = subprocess.run([sys.executable, "run.py", url, "--fresh"], env=os.environ.copy())
    final = os.path.join(OUT_DIR, FINAL_NAME)
    return result.returncode == 0 and os.path.exists(final)


def stash_output(dest_dir: str):
    """Copy the just-built output/ into the video's own keep folder, so the next --fresh run
    (which wipes output/) can't destroy it. dirs_exist_ok lets a re-make overwrite cleanly."""
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    shutil.copytree(OUT_DIR, dest_dir, dirs_exist_ok=True)


def confirm(n: int) -> bool:
    """The cost gate: show how many videos and the rough total, then wait for a yes."""
    est = n * PER_VIDEO_EST
    print("\n" + "=" * 60)
    print(f"  ABOUT TO MAKE {n} VIDEO(S)")
    print("=" * 60)
    print(f"  Rough estimate: {n} x ~${PER_VIDEO_EST:.2f} = ~${est:.2f}")
    print("  (rough only — the REAL cost is printed per video and totalled at the end)")
    print("=" * 60)
    ans = input("  Type 'yes' to build, anything else to cancel: ").strip().lower()
    return ans in ("y", "yes")


def main():
    args = sys.argv[1:]
    assume_yes = "--yes" in args or "-y" in args
    dry_run = "--dry-run" in args
    cap = BATCH_MAX
    if "--max" in args:                      # --max N overrides the batch size for this run
        try:
            cap = int(args[args.index("--max") + 1])
        except (ValueError, IndexError):
            sys.exit("--max needs a number, e.g. --max 1")

    queue = load_queue()
    pending = [r for r in queue if r.get("status") == "pending"]
    pending.sort(key=lambda r: r.get("overall", 0), reverse=True)
    if not pending:
        print("Nothing pending in the queue. Run discover.py first to find + pick stories.")
        return

    batch = pending[:cap]
    print(f"{len(pending)} story(ies) pending; making the top {len(batch)} this run "
          f"(cap {cap}, highest score first):")
    for r in batch:
        print(f"  {r.get('overall', 0):>4.1f}/10  {r.get('title', '')[:60]}")

    if dry_run:
        print(f"\n[dry run] would make {len(batch)} video(s), rough estimate "
              f"~${len(batch) * PER_VIDEO_EST:.2f}. Nothing was built.")
        return

    if not assume_yes and not confirm(len(batch)):
        print("Cancelled — nothing was built.")
        return

    date = time.strftime("%Y-%m-%d")
    made, failed = [], []
    run_start = time.time()

    for i, r in enumerate(batch, 1):
        url = r["url"]
        print("\n" + "#" * 60)
        print(f"#  VIDEO {i}/{len(batch)}  ({r.get('overall', 0):.1f}/10)  {r.get('title', '')[:45]}")
        print("#" * 60, flush=True)

        try:
            ok = build_one(url)
        except Exception as e:
            print(f"  build crashed ({type(e).__name__}: {e})")
            ok = False

        if not ok:
            # Skip-and-continue: leave it "pending" so a later run can retry it, and move on.
            print(f"  VIDEO {i} FAILED — left in the queue to retry later.")
            failed.append(r)
            continue

        dest = os.path.join(VIDEOS_DIR, f"{date}-{url_slug(url)}")
        stash_output(dest)
        # Mark this story made in the queue (with where its video lives) so it's never remade.
        r["status"] = "made"
        r["video_dir"] = dest
        r["made_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_queue(queue)      # save after each one, so a mid-batch crash doesn't lose progress
        print(f"  VIDEO {i} DONE -> {os.path.join(dest, FINAL_NAME)}")
        made.append((r, dest))

    # Batch summary with the REAL total, read back from each finished video's recorded costs.
    print("\n" + "=" * 60)
    print("  BATCH DONE")
    print("=" * 60)
    print(f"  Made:   {len(made)}")
    print(f"  Failed: {len(failed)}")
    total = 0.0
    for r, dest in made:
        c = real_cost(dest)
        total += c
        print(f"    ${c:>6.2f}  {os.path.join(dest, FINAL_NAME)}")
    for r in failed:
        print(f"    FAILED  {r.get('title', '')[:50]}  ({r['url']})")
    if made:
        print("  " + "-" * 56)
        print(f"  REAL TOTAL for this batch: ${total:.2f}")
    print(f"  Batch time: {costs.fmt_duration(time.time() - run_start)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
