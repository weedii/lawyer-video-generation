"""THE MANAGER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. scene_writer.py    -> write the SCENE script (who is in each scene + dialogue)
    4. gen_characters.py  -> make one locked portrait per USED character (only the
                             characters the script actually uses, to skip wasted renders)
    5. voice_maker.py     -> clone each character's voice + narrator voiceover
    6. scene_clips.py     -> render one cinematic SCENE clip per beat (Kling)
    7. assemble.py        -> join the scenes into the final video
The result is output/final_video.mp4.

Usage:
    python run.py "https://www.rollonfriday.com/news-content/some-story"

Each sub-script prints its own result and cost.
"""
import sys
import os
import json
import subprocess
import costs


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
    if len(sys.argv) < 2:
        sys.exit('Usage: python run.py "<story-url>"')

    url = sys.argv[1]

    # The steps, in order. scrape needs the link; the others read files.
    step("STEP 1/7", "Scrape the story", ["scrape.py", url])
    step("STEP 2/7", "Analyze story + invent characters", ["analyze.py"])
    # Write the script BEFORE drawing faces, so we only pay to draw the characters
    # the script actually uses (analyze often invents extras the story never needs).
    step("STEP 3/7", "Write the scene script", ["scene_writer.py"])
    step("STEP 4/7", "Make a locked portrait for each USED character", ["gen_characters.py"])
    step("STEP 5/7", "Clone character voices + narrator voiceover", ["voice_maker.py"])
    step("STEP 6/7", "Render one cinematic scene clip per beat", ["scene_clips.py"])
    step("STEP 7/7", "Join the scenes into the final video", ["assemble.py"])

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
