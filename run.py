"""THE MANAGER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. gen_characters.py  -> make one image per character
    4. scene_writer.py    -> write the scene script (who says what)
    5. voice_maker.py     -> make the voice for every line
Then it STOPS.

Usage:
    python run.py "https://www.rollonfriday.com/news-content/some-story"

Each sub-script prints its own result and cost.
"""
import sys
import subprocess


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
    step("STEP 1/5", "Scrape the story", ["scrape.py", url])
    step("STEP 2/5", "Analyze story + invent characters", ["analyze.py"])
    step("STEP 3/5", "Make an image for each character", ["gen_characters.py"])
    step("STEP 4/5", "Write the scene script", ["scene_writer.py"])
    step("STEP 5/5", "Make the voice for every line", ["voice_maker.py"])

    print("\n" + "=" * 55, flush=True)
    print("  ALL DONE", flush=True)
    print("=" * 55, flush=True)
    print("Read everything:  output/analysis.md", flush=True)
    print("Character images: output/char_*.png", flush=True)
    print("Voice lines:      output/voice_*.mp3", flush=True)
