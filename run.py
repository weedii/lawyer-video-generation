"""THE RUNNER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. scene_writer.py    -> write the SCENE script (who is in each scene + dialogue)
    4. gen_characters.py  -> make one locked portrait per USED character (only the
                             characters the script actually uses, to skip wasted renders)
    5. voice_maker.py     -> assign each character a voice (and pick the random narrator voice)
    6. audio_maker.py     -> make ALL the audio (voiceover + ambience + music), ElevenLabs
    7. scene_clips.py     -> render one silent SCENE clip per beat (Seedance) + lay the voiceover
    8. assemble.py        -> join the scenes into the final video
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
    # Parse the command line: one URL plus optional flags that skip the questions.
    #   --fresh / --repair / --scan                whole-run modes
    #   --redo 6        or --redo=3,6              re-render just those scene CLIPS
    #   --redo-image 6  or --redo-image=3,6        re-make those scenes' IMAGE + clip
    flag = ""
    redo_scenes = ""            # the "3,6" list from --redo / --redo-image
    redo_with_image = False     # True only for --redo-image
    positional = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--fresh", "--repair", "--scan"):
            flag = a.lstrip("-")
        elif a in ("--redo", "--redo-image"):
            flag = "redo"
            redo_with_image = (a == "--redo-image")
            # the scene list is the next token (e.g. "--redo 6"); allow it to be missing
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                redo_scenes = args[i + 1]
                i += 1
        elif a.startswith("--redo=") or a.startswith("--redo-image="):
            flag = "redo"
            redo_with_image = a.startswith("--redo-image=")
            redo_scenes = a.split("=", 1)[1]
        else:
            positional.append(a)
        i += 1
    # The URL: given on the line, or (for --repair/--scan/--redo on an existing run) reused
    # from the last run so you don't have to paste the same link again.
    url = positional[0] if positional else manager.previous_url()
    if not url:
        sys.exit('Usage: python run.py "<story-url>"  '
                 '[--fresh | --repair | --scan | --redo N | --redo-image N]')

    # Ask the manager HOW to run: fresh build, repair the last run, just scan, or cancel.
    mode = manager.decide_mode(url, flag)

    # SCAN or CANCEL: no building. Scan prints the health table of the last run and stops.
    if mode in ("scan", "cancel"):
        if mode == "scan":
            manager.print_health(manager.health_check(manager.load_json(manager.ANALYSIS)))
        else:
            print("Cancelled — nothing was changed.")
        sys.exit(0)

    # REDO specific scenes: pick which (from the flag or by asking), delete just those files,
    # and let the resume-guarded steps rebuild only them. Done BEFORE the clock so the time
    # spent choosing isn't counted as run time.
    if mode == "redo":
        data = manager.load_json(manager.ANALYSIS)
        if not data or not (data.get("script") or {}).get("scenes"):
            sys.exit("No previous run found to redo. Build a video first (fresh run).")
        # Where the picks come from: a flag (--redo 3,6) skips the questions; otherwise ask.
        if redo_scenes:
            targets = manager.redo_targets_from_flag(redo_scenes, redo_with_image, data)
        else:
            targets = manager.pick_redo_targets(data)
        if not targets:
            print("Nothing picked — nothing was changed.")
            sys.exit(0)
        picks = ", ".join(f"scene {t['scene']}"
                          f"{' (new image + clip)' if t['with_image'] else ' (clip only)'}"
                          for t in targets)
        print(f"\nRedoing: {picks}")
        print(f"Estimated cost: ~${manager.estimate_redo(targets):.2f}  "
              f"(the real cost is printed at the end)")
        run_start = time.time()
        print("\nRemoving the chosen files so they get rebuilt ...")
        manager.apply_redo(targets)
        # audio_maker reuses the voiceovers already on disk (a redo doesn't change the words),
        # and only regenerates one if it's missing — so the re-rendered clip has its voice.
        step("REDO 1/3", "Make sure the audio exists (voiceover + ambience + music)", ["audio_maker.py"])
        step("REDO 2/3", "Re-render the picked scene clip(s)", ["scene_clips.py"])
        step("REDO 3/3", "Re-join everything into the final video", ["assemble.py"])
        manager.save_state(url, "redo", manager.health_check(manager.load_json(manager.ANALYSIS)))
        print("\n" + "=" * 55, flush=True)
        print("  REDO DONE", flush=True)
        print("=" * 55, flush=True)
        print("Final video:      output/final_video.mp4", flush=True)
        if os.environ.get("COSTLOG"):
            subprocess.run([sys.executable, "reconcile.py"])
        else:
            try:
                with open(os.path.join("output", "analysis.json")) as f:
                    costs.print_summary(json.load(f))
            except Exception as e:
                print(f"(could not print cost summary: {e})", flush=True)
        print(f"\nTotal redo time:  {costs.fmt_duration(time.time() - run_start)}", flush=True)
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
        step("REPAIR 1/4", "Re-make any missing character portraits", ["gen_characters.py"])
        step("REPAIR 2/4", "Re-make any missing audio (voiceover + ambience + music)", ["audio_maker.py"])
        step("REPAIR 3/4", "Re-render any missing/broken scene clips", ["scene_clips.py"])
        step("REPAIR 4/4", "Re-join everything into the final video", ["assemble.py"])
    else:
        # FRESH: start from zero. Empty the folder first so nothing from an old run leaks in,
        # then run all 8 steps. scrape needs the link; the others read files.
        manager.wipe_output()
        step("STEP 1/8", "Scrape the story", ["scrape.py", url])
        step("STEP 2/8", "Analyze story + invent characters", ["analyze.py"])
        # Write the script BEFORE drawing faces, so we only pay to draw the characters
        # the script actually uses (analyze often invents extras the story never needs).
        step("STEP 3/8", "Write the scene script", ["scene_writer.py"])
        step("STEP 4/8", "Make a locked portrait for each USED character", ["gen_characters.py"])
        step("STEP 5/8", "Assign each character a voice (+ random narrator)", ["voice_maker.py"])
        step("STEP 6/8", "Make all the audio (voiceover + ambience + music)", ["audio_maker.py"])
        step("STEP 7/8", "Render one silent scene clip per beat + lay the voiceover", ["scene_clips.py"])
        step("STEP 8/8", "Join the scenes into the final video", ["assemble.py"])

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
