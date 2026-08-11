"""THE RUN MANAGER — decides HOW a run should go, so run.py stays simple.

run.py is the list of 8 steps. This file is the brain that sits in front of it and
answers one question: "we're about to build a video — should we start fresh, or is
there a half-finished run we can just repair?"

It does five things:
  1. REMEMBER the last run (output/run_state.json: which link, when, how it went).
  2. DETECT the situation on start (empty folder? same link as last time? a different
     video sitting in the folder?).
  3. ASK the user in plain English what they want (start over / repair / scan / redo).
  4. SCAN ("doctor") every file the last run made and say what is OK, MISSING or BROKEN —
     so REPAIR mode can re-make ONLY the broken/missing pieces and skip everything good.
  5. REDO specific scenes the user picks by number (even ones that aren't broken), the clip
     alone or the image + clip, and nothing else.

Why repair/redo are safe here: steps 2 (analyze) and 3 (scene_writer) are AI and give a
DIFFERENT story every time (different names, different scenes). Re-running them would
throw away the portraits and clips that matched the OLD story. So repair/redo NEVER re-run
them — they reuse the existing output/analysis.json (same story, same names, same voice)
and only fill the gaps. The trick for "re-make only what's broken/picked": the doctor
(repair) or the redo picker DELETES the target files, then the normal steps (which already
reuse whatever is on disk) naturally regenerate exactly those files and nothing else.

Costs: SCAN is free. REPAIR only pays for the few clips it re-makes (~$0.29 each), and REDO
only for the scenes you pick (~$0.21 clip only, ~$0.29 image + clip), instead of a whole
~$2 rebuild. START OVER costs the full price, like a normal run.
"""
import os
import json
import time
import shutil
import subprocess
import costs

OUT_DIR = "output"
ANALYSIS = os.path.join(OUT_DIR, "analysis.json")
SCRAPED = os.path.join(OUT_DIR, "scraped.json")
STATE = os.path.join(OUT_DIR, "run_state.json")
MUSIC = os.path.join(OUT_DIR, "music.mp3")
FINAL = os.path.join(OUT_DIR, "final_video.mp4")

# A clip shorter than this many seconds is treated as broken (a crash mid-render can
# leave a tiny truncated file that would otherwise be "reused" and wreck the video).
MIN_CLIP_SECS = 1.0
# Rough price of re-making one scene clip: Seedance ~8s silent (~$0.21) + one composed
# image on Nano Banana 2 at 1K ($0.08). Used only to show the user an estimate before a
# repair; the real cost is printed after the run.
REPAIR_PER_CLIP = 0.29
# Typical clip length (seconds) used ONLY for the pre-run redo estimate; the printed cost
# after the run is the real one.
REDO_CLIP_SECS = 8


# --------------------------------------------------------------------------- files

def load_json(path: str):
    """Read a JSON file, or return None if it isn't there / is unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _ffprobe(path: str) -> dict:
    """Ask ffprobe what's inside a media file: which stream types it has and how long it
    is. Returns {"video": bool, "audio": bool, "dur": float}. On any failure (file missing
    or unreadable) returns all-empty, so the caller treats it as broken."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=codec_type",
             "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, check=True).stdout
        info = json.loads(out)
        kinds = [s.get("codec_type") for s in info.get("streams", [])]
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        return {"video": "video" in kinds, "audio": "audio" in kinds, "dur": dur}
    except Exception:
        return {"video": False, "audio": False, "dur": 0.0}


def _valid_image(path: str) -> bool:
    """A portrait counts as real if it exists and isn't a tiny stub (a dropped download can
    leave a near-empty .png). We keep this cheap — no full image decode."""
    return os.path.exists(path) and os.path.getsize(path) > 1024


# --------------------------------------------------------------------------- the doctor

def health_check(data: dict) -> list:
    """Scan everything the last run should have produced and return a list of findings,
    each: {"kind","name","path","status","note"} where status is "ok" | "missing" | "broken".

    Built from analysis.json (the fixed story), so it checks the RIGHT files by name:
      - one portrait per USED character,
      - one clip per scene (must open, have a picture, be long enough, and — when the
        scene has narration — carry the voiceover audio track),
      - the per-location ambience beds and the music bed,
      - the final video (flagged STALE if older than the newest clip).
    """
    scenes = (data.get("script") or {}).get("scenes", []) if data else []
    chars = {c.get("fictional_name"): c for c in (data.get("characters", []) if data else [])}
    report = []

    # Which characters actually appear on screen (only those need a portrait).
    used = set()
    for sc in scenes:
        used.update(sc.get("onscreen") or sc.get("characters", []))
    for name in sorted(used):
        c = chars.get(name)
        if not c:
            continue
        f = c.get("file")
        path = os.path.join(OUT_DIR, f) if f else ""
        if not f or not os.path.exists(path):
            report.append({"kind": "character", "name": name, "path": path,
                           "status": "missing", "note": "no portrait"})
        elif not _valid_image(path):
            report.append({"kind": "character", "name": name, "path": path,
                           "status": "broken", "note": "portrait file is empty/tiny"})
        else:
            report.append({"kind": "character", "name": name, "path": path,
                           "status": "ok", "note": ""})

    # One clip per scene. Scene index is 1-based (clip_01.mp4 ...), matching scene_clips.py.
    newest_clip = 0.0
    for i, sc in enumerate(scenes, 1):
        clip = f"clip_{i:02d}.mp4"
        path = os.path.join(OUT_DIR, clip)
        label = f"Scene {i} ({sc.get('type','scene')})"
        needs_audio = bool((sc.get("narration") or "").strip())   # VO scenes must have a voice track
        if not os.path.exists(path):
            report.append({"kind": "clip", "name": label, "path": path,
                           "status": "missing", "note": clip})
            continue
        probe = _ffprobe(path)
        newest_clip = max(newest_clip, os.path.getmtime(path))
        if not probe["video"] or probe["dur"] < MIN_CLIP_SECS:
            report.append({"kind": "clip", "name": label, "path": path, "status": "broken",
                           "note": f"{clip}: no picture or too short ({probe['dur']:.1f}s)"})
        elif needs_audio and not probe["audio"]:
            report.append({"kind": "clip", "name": label, "path": path, "status": "broken",
                           "note": f"{clip}: missing the voiceover track"})
        else:
            report.append({"kind": "clip", "name": label, "path": path, "status": "ok",
                           "note": f"{clip}  {probe['dur']:.1f}s"})

    # Ambience beds (one per location referenced by a scene) + the music bed. Light check
    # (exists + not empty) — these are cheap to remake and not worth an ffprobe each.
    beds = sorted({sc.get("ambient") for sc in scenes if sc.get("ambient")})
    for bed in beds:
        path = os.path.join(OUT_DIR, bed)
        ok = os.path.exists(path) and os.path.getsize(path) > 1024
        report.append({"kind": "ambience", "name": f"Ambience {bed}", "path": path,
                       "status": "ok" if ok else "missing", "note": "" if ok else "room sound"})
    music_ok = os.path.exists(MUSIC) and os.path.getsize(MUSIC) > 1024
    report.append({"kind": "music", "name": "Music bed (music.mp3)", "path": MUSIC,
                   "status": "ok" if music_ok else "missing", "note": ""})

    # The final joined video. If it's older than the newest clip, it doesn't reflect the
    # latest clips yet — flag STALE so repair always re-joins at the end.
    if not os.path.exists(FINAL):
        report.append({"kind": "final", "name": "Final video (final_video.mp4)", "path": FINAL,
                       "status": "missing", "note": "not built yet"})
    elif newest_clip and os.path.getmtime(FINAL) < newest_clip:
        report.append({"kind": "final", "name": "Final video (final_video.mp4)", "path": FINAL,
                       "status": "broken", "note": "older than the clips — needs re-joining"})
    else:
        report.append({"kind": "final", "name": "Final video (final_video.mp4)", "path": FINAL,
                       "status": "ok", "note": ""})
    return report


def print_health(report: list):
    """Print the scan as a simple aligned table anyone can read at a glance."""
    mark = {"ok": "OK", "missing": "MISSING", "broken": "BROKEN"}
    print("\n" + "-" * 60)
    print("  SCAN OF THE LAST RUN  (output/)")
    print("-" * 60)
    for r in report:
        dots = "." * max(3, 40 - len(r["name"]))
        extra = f"  {r['note']}" if r["note"] else ""
        print(f"  {r['name']} {dots} {mark[r['status']]}{extra}")
    ok = sum(1 for r in report if r["status"] == "ok")
    miss = sum(1 for r in report if r["status"] == "missing")
    bad = sum(1 for r in report if r["status"] == "broken")
    n_clip_fix = sum(1 for r in report if r["kind"] == "clip" and r["status"] != "ok")
    print("-" * 60)
    print(f"  {ok} OK, {miss} missing, {bad} broken")
    if n_clip_fix:
        print(f"  Repair would re-make {n_clip_fix} clip(s)  (~${n_clip_fix * REPAIR_PER_CLIP:.2f})")
    print("-" * 60)


def delete_broken(report: list) -> int:
    """Remove every file the scan marked BROKEN, so the normal steps (which reuse whatever
    is on disk and only build what's missing) regenerate exactly those. MISSING files are
    already absent, so they need no action. Returns how many were deleted."""
    n = 0
    for r in report:
        if r["status"] == "broken" and r["path"] and os.path.exists(r["path"]):
            # The final video is "broken" only in the STALE sense — assemble always rewrites
            # it at the end, so no need to delete it here.
            if r["kind"] == "final":
                continue
            try:
                os.remove(r["path"])
                print(f"    removed broken {os.path.basename(r['path'])} (will be rebuilt)")
                n += 1
            except OSError:
                pass
    return n


# --------------------------------------------------------------------------- redo specific parts

def scene_list(data: dict) -> list:
    """The scenes of the last run, each as (number, type, beat, who, one-line description),
    1-based to match clip_01.mp4 ... — used to show the redo menu."""
    scenes = (data.get("script") or {}).get("scenes", []) if data else []
    rows = []
    for i, sc in enumerate(scenes, 1):
        who = ", ".join(sc.get("onscreen") or sc.get("characters", [])) or "—"
        desc = (sc.get("narration") or sc.get("action") or "").strip()
        rows.append((i, sc.get("type", "scene"), sc.get("beat", ""), who, desc[:60]))
    return rows


def parse_scene_numbers(raw: str, valid: set) -> list:
    """Turn '3,6' (or '3 6') into a sorted list of valid scene numbers; junk is ignored."""
    out = []
    for tok in raw.replace(",", " ").split():
        if tok.isdigit() and int(tok) in valid and int(tok) not in out:
            out.append(int(tok))
    return sorted(out)


def _redo_files(scene_i: int, with_image: bool) -> list:
    """Files to delete for one scene redo: the clip always; the composed still too if the
    user wants a NEW picture (not just a re-animation of the same one)."""
    files = [os.path.join(OUT_DIR, f"clip_{scene_i:02d}.mp4")]
    if with_image:
        files.append(os.path.join(OUT_DIR, f"scene_{scene_i:02d}.png"))
    return files


def _clip_cost() -> float:
    return REDO_CLIP_SECS * costs.SEEDANCE_PRO_NOAUDIO_PER_SEC


def _image_clip_cost() -> float:
    return _clip_cost() + costs.NANO_BANANA_2_EDIT_PER_IMAGE


def estimate_redo(targets: list) -> float:
    """Rough $ for a set of redo targets (real cost is printed after the run)."""
    return sum(_image_clip_cost() if t["with_image"] else _clip_cost() for t in targets)


def apply_redo(targets: list) -> int:
    """Delete EXACTLY the chosen files, so the resume-guarded steps rebuild only those and
    nothing else is touched. Prints each deletion. Returns how many files were removed."""
    n = 0
    for t in targets:
        for p in _redo_files(t["scene"], t["with_image"]):
            if os.path.exists(p):
                os.remove(p)
                print(f"    removed {os.path.basename(p)} (will be rebuilt)")
                n += 1
    return n


def zero_spent():
    """Reset the 'paid this run' tally on analysis.json before a REDO/REPAIR, so the final cost
    table can show what THIS run actually cost separately from the video's full price. Every
    reused piece keeps its real price in the total but counts $0 as spent now; the steps that
    run this time write their own spent back over the zero."""
    data = load_json(ANALYSIS)
    if not data:
        return
    costs.reset_spent(data)
    with open(ANALYSIS, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def redo_targets_from_flag(scene_str: str, with_image: bool, data: dict) -> list:
    """Build redo targets from a --redo/--redo-image flag value like '3,6' (no questions)."""
    valid = {i for i, *_ in scene_list(data)}
    return [{"scene": i, "with_image": with_image}
            for i in parse_scene_numbers(scene_str, valid)]


def pick_redo_targets(data: dict) -> list:
    """Interactive redo: show the scene list, ask which scene(s) to redo, then for each ask
    how deep (clip only vs image + clip). Returns [{"scene": i, "with_image": bool}, ...],
    or [] if the user picks nothing."""
    rows = scene_list(data)
    if not rows:
        print("  No scenes found — nothing to redo.")
        return []
    print("\n  Scenes in the last run:")
    for i, typ, beat, who, desc in rows:
        print(f"    [{i}] {typ:9} {beat:12} {who}")
        if desc:
            print(f"         “{desc}”")
    valid = {i for i, *_ in rows}
    raw = input("\n  Which scene(s) do you want to redo? (e.g. 6 or 3,6) "
                "— or press Enter to cancel: ").strip()
    picks = parse_scene_numbers(raw, valid)
    if not picks:
        return []
    targets = []
    for i in picks:
        print(f"\n  Scene {i} — how deep do you want to redo it?")
        print(f"    [1] CLIP ONLY — keep the same picture, just re-animate the movement "
              f"(~${_clip_cost():.2f}).")
        print("        Use this when the picture is fine but the movement was wrong.")
        print(f"    [2] IMAGE + CLIP — make a NEW picture, then animate it "
              f"(~${_image_clip_cost():.2f}).")
        print("        Use this when the picture itself is wrong.")
        depth = _ask("    Type 1 or 2: ", {"1", "2"})
        targets.append({"scene": i, "with_image": depth == "2"})
    return targets


# --------------------------------------------------------------------------- start folder

# Files the user sets up ONCE and reuses across every video — a wipe must keep these.
# look.cube is an optional creative colour LUT (a persistent brand grade) that assemble.py
# applies if present; deleting it on "start over" would silently drop the channel's look.
KEEP_ON_WIPE = {"look.cube"}


def wipe_output():
    """START OVER: empty the output folder so the run builds from zero. Deletes files only
    (leaves the folder itself), keeps the reusable brand assets in KEEP_ON_WIPE, and never
    touches anything outside output/."""
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR, exist_ok=True)
        return
    for name in os.listdir(OUT_DIR):
        if name in KEEP_ON_WIPE:
            continue
        p = os.path.join(OUT_DIR, name)
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
            else:
                shutil.rmtree(p)
        except OSError:
            pass


def has_previous_run() -> bool:
    """True if the folder holds a previous video's work (a story file or any clip)."""
    if load_json(ANALYSIS):
        return True
    return os.path.isdir(OUT_DIR) and any(
        n.startswith("clip_") and n.endswith(".mp4") for n in os.listdir(OUT_DIR))


def previous_url() -> str:
    """The link the last run was built from (stored in scraped.json), or '' if unknown."""
    data = load_json(SCRAPED) or {}
    return data.get("url", "") or ""


# --------------------------------------------------------------------------- ask the user

def _ask(prompt: str, choices: set) -> str:
    """Ask on the terminal until the user types one of the allowed choices."""
    while True:
        ans = input(prompt).strip().lower()
        if ans in choices:
            return ans
        print(f"  Please type one of: {', '.join(sorted(choices))}")


def decide_mode(url: str, flag: str = "") -> str:
    """Work out how this run should go and return one of:
        "fresh"  — build the whole video from zero (wiping any old files first),
        "repair" — keep the last run's story/voice and only fix missing/broken pieces,
        "scan"   — just print the health of the last run and stop (spends nothing),
        "cancel" — do nothing.

    A flag ("fresh" | "repair" | "scan") forces the choice with no questions — handy for
    the future automated pipeline. With no flag, it looks at the folder and, only when
    there IS something to protect, asks the user in plain English.
    """
    if flag in ("fresh", "repair", "scan", "redo"):
        return flag

    # Nothing there yet -> just build it, no need to ask.
    if not has_previous_run():
        return "fresh"

    same_link = url and previous_url() == url
    if same_link:
        print("\n" + "=" * 60)
        print("  You already made a video from THIS SAME link before.")
        print("=" * 60)
        print("  What do you want to do?\n")
        print("  [1] START OVER (fresh)")
        print("      Delete everything in the output folder and build the whole")
        print("      video again from scratch. Full price (about $2). Pick this")
        print("      if you want a brand-new take on the story.\n")
        print("  [2] REPAIR (fix what's broken)")
        print("      Keep last time's story, characters and narrator voice. Only")
        print("      re-make the clips that are missing or broken, then rebuild the")
        print("      final video. Cheap — you only pay for the few pieces to fix.\n")
        print("  [3] JUST SCAN (look, don't spend)")
        print("      Only show me what's OK, missing or broken from last time, then")
        print("      stop. Costs nothing.\n")
        print("  [4] PICK PARTS TO REDO (fix specific scenes)")
        print("      Choose one or more scenes by number and re-make just those — the")
        print("      picture, the movement, or both. Everything else is kept. Use this")
        print("      when a scene came out wrong even though it isn't broken.\n")
        print("  [5] CANCEL — do nothing and quit.\n")
        pick = _ask("  Type 1, 2, 3, 4 or 5 and press Enter: ", {"1", "2", "3", "4", "5"})
        return {"1": "fresh", "2": "repair", "3": "scan", "4": "redo", "5": "cancel"}[pick]

    # Folder has a DIFFERENT video in it.
    print("\n" + "=" * 60)
    print("  The output folder already has a DIFFERENT video in it")
    print("  (made from another link).")
    print("=" * 60)
    print("  [1] START OVER — delete that old video and build this new link instead.")
    print("      (If you want to keep the old one, cancel and copy it somewhere first.)\n")
    print("  [2] CANCEL — stop and change nothing.\n")
    pick = _ask("  Type 1 or 2 and press Enter: ", {"1", "2"})
    return {"1": "fresh", "2": "cancel"}[pick]


# --------------------------------------------------------------------------- remember

def save_state(url: str, mode: str, report: list = None):
    """Write output/run_state.json so the next run knows what this one did."""
    counts = {"ok": 0, "missing": 0, "broken": 0}
    if report:
        for r in report:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
    state = {
        "url": url,
        "mode": mode,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "final_video": os.path.exists(FINAL),
        "health": counts,
    }
    try:
        with open(STATE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass
