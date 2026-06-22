"""THE MANAGER: run the whole workflow from one command.

You give it a story link. It runs the other scripts in order:
    1. scrape.py          -> download the story
    2. analyze.py         -> organize it + invent characters
    3. gen_characters.py  -> make one image per character
Then it STOPS (character images are the end of this stage).

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

    # The three stages, in order. scrape needs the link; the others read files.
    step("STEP 1/3", "Scrape the story", ["scrape.py", url])
    step("STEP 2/3", "Analyze story + invent characters", ["analyze.py"])
    step("STEP 3/3", "Make an image for each character", ["gen_characters.py"])

    print("\n" + "=" * 55, flush=True)
    print("  ALL DONE", flush=True)
    print("=" * 55, flush=True)
    print("Read the story:   output/analysis.md", flush=True)
    print("Character images: output/char_*.png", flush=True)
