"""THE RUNNER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. scene_writer.py    -> write the SCENE script (who is in each scene + dialogue)
    4. gen_characters.py  -> make one locked portrait per USED character (only the
                             characters the script actually uses, to skip wasted renders)
    5. voice_maker.py     -> clone each character's voice + narrator voiceover
    6. scene_clips.py     -> render one cinematic SCENE clip per beat (Seedance)
    7. assemble.py        -> join the scenes into the final video
The result is output/final_video.mp4.

Before it starts, manager.py checks whether you've already run this SAME link. If so it
asks (in plain English) whether to START OVER, REPAIR just the broken/missing pieces, or
just SCAN and stop. REPAIR reuses the last run's story + voice and only re-makes what's
broken — much cheaper than a full rebuild. See manager.py for the details.

Usage:
    python run.py "https://www.rollonfriday.com/news-content/some-story"
    python run.py "<url>" --repair    # skip the questions: fix broken/missing pieces only
    python run.py "<url>" --fresh      # skip the questions: wipe and rebuild from zero
    python run.py "<url>" --scan       # just check the last run's health, build nothing

Each sub-script prints its own result and cost.
"""
import sys
import os
import json
import time
import subprocess
import costs
import manager


def step(number: str, title: str, script_args: list[str]):
    """Run one sub-script and stop everything if it fails."""
    # flush=True makes the header appear BEFORE the sub-script's output,
    # not buffered until the end.
    print("\n" + "=" * 55, flush=True)
    print(f"  {number}  {title}", flush=True)
    print("=" * 55, flush=True)
    # sys.executable = the same Python (our .venv), so it uses our installed tools.
    subprocess.run([sys.executable] + script_args, check=True)


if __name__ == "__main__":
    # Parse the command line: one URL plus optional --fresh / --repair / --scan.
    # A flag skips the questions (useful for automation); a bare word is the URL.
    flag = ""
    positional = []
    for a in sys.argv[1:]:
        if a in ("--fresh", "--repair", "--scan"):
            flag = a.lstrip("-")
        else:
            positional.append(a)
    # The URL: given on the line, or (for --repair/--scan on an existing run) reused from
    # the last run so you don't have to paste the same link again.
    url = positional[0] if positional else manager.previous_url()
    if not url:
        sys.exit('Usage: python run.py "<story-url>"  [--fresh | --repair | --scan]')

    # Ask the manager HOW to run: fresh build, repair the last run, just scan, or cancel.
    mode = manager.decide_mode(url, flag)

    # SCAN or CANCEL: no building. Scan prints the health table of the last run and stops.
    if mode in ("scan", "cancel"):
        if mode == "scan":
            manager.print_health(manager.health_check(manager.load_json(manager.ANALYSIS)))
        else:
            print("Cancelled — nothing was changed.")
        sys.exit(0)

    # Start the clock so we can report how long the WHOLE run took.
    run_start = time.time()

    if mode == "repair":
        # REPAIR: keep last time's story, characters and narrator voice. Scan first, delete
        # the broken files (so the steps below rebuild exactly those), then run ONLY the
        # asset steps. We deliberately SKIP scrape/analyze/scene_writer (they are AI and would
        # invent a different story) and voice_maker (it would pick a new random narrator
        # voice) — all of that is reused from the existing output/analysis.json.
        report = manager.health_check(manager.load_json(manager.ANALYSIS))
        manager.print_health(report)
        manager.delete_broken(report)
        step("REPAIR 1/3", "Re-make any missing character portraits", ["gen_characters.py"])
        step("REPAIR 2/3", "Re-render any missing/broken scene clips", ["scene_clips.py"])
        step("REPAIR 3/3", "Re-join everything into the final video", ["assemble.py"])
    else:
        # FRESH: start from zero. Empty the folder first so nothing from an old run leaks in,
        # then run all 7 steps. scrape needs the link; the others read files.
        manager.wipe_output()
        step("STEP 1/7", "Scrape the story", ["scrape.py", url])
        step("STEP 2/7", "Analyze story + invent characters", ["analyze.py"])
        # Write the script BEFORE drawing faces, so we only pay to draw the characters
        # the script actually uses (analyze often invents extras the story never needs).
        step("STEP 3/7", "Write the scene script", ["scene_writer.py"])
        step("STEP 4/7", "Make a locked portrait for each USED character", ["gen_characters.py"])
        step("STEP 5/7", "Clone character voices + narrator voiceover", ["voice_maker.py"])
        step("STEP 6/7", "Render one cinematic scene clip per beat", ["scene_clips.py"])
        step("STEP 7/7", "Join the scenes into the final video", ["assemble.py"])

    # Remember what this run did, so the NEXT run can detect it and offer repair.
    manager.save_state(url, mode, manager.health_check(manager.load_json(manager.ANALYSIS)))

    print("\n" + "=" * 55, flush=True)
    print("  ALL DONE", flush=True)
    print("=" * 55, flush=True)
    print("Final video:      output/final_video.mp4", flush=True)
    print("Read everything:  output/analysis.md", flush=True)

    # Cost of this video. When the API spy is on (COSTLOG=1), print the REAL cost
    # computed from the ACTUAL billed units in output/api_calls.jsonl (no
    # estimation). Otherwise fall back to the per-step estimate in analysis.json.
    if os.environ.get("COSTLOG"):
        subprocess.run([sys.executable, "reconcile.py"])
    else:
        try:
            with open(os.path.join("output", "analysis.json")) as f:
                costs.print_summary(json.load(f))
        except Exception as e:
            print(f"(could not print cost summary: {e})", flush=True)

    # How long the whole run took, printed AFTER the cost table.
    print(f"\nTotal run time:   {costs.fmt_duration(time.time() - run_start)}", flush=True)
