"""THE MANAGER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. gen_characters.py  -> make one image per character
    4. scene_writer.py    -> write the scene script (who says what)
    5. voice_maker.py     -> make the voice for every line
    6. talking_clips.py   -> make a talking clip for every line
    7. assemble.py        -> join the clips into the final video
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
    step("STEP 3/7", "Make an image for each character", ["gen_characters.py"])
    step("STEP 4/7", "Write the scene script", ["scene_writer.py"])
    step("STEP 5/7", "Make the voice for every line", ["voice_maker.py"])
    step("STEP 6/7", "Make a talking clip for every line", ["talking_clips.py"])
    step("STEP 7/7", "Join the clips into the final video", ["assemble.py"])

    print("\n" + "=" * 55, flush=True)
    print("  ALL DONE", flush=True)
    print("=" * 55, flush=True)
    print("Final video:      output/final_video.mp4", flush=True)
    print("Read everything:  output/analysis.md", flush=True)

    # Print the whole cost of this video: each step recorded its real cost into
    # analysis.json as it ran; here we total it and show the breakdown.
    try:
        with open(os.path.join("output", "analysis.json")) as f:
            costs.print_summary(json.load(f))
    except Exception as e:
        print(f"(could not print cost summary: {e})", flush=True)
