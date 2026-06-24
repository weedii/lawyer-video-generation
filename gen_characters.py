"""STEP C: Make one image for EVERY character found in the story.

It reads the analysis file (from analyze.py), takes each character's
ready-made image prompt, and generates one vertical image per character
with fal.ai. It also saves a small index file so the next steps know which
picture belongs to which character.

Usage:
    python gen_characters.py

Reads:  output/analysis.json    (from analyze.py)
Output: output/char_<name>.png  (one image per character)
        It also writes the image file name back INTO analysis.json (each
        character gets a "file" field), so everything stays in one place.
Cost:   $0.15 per character image (Nano Banana Pro).
"""
import os
import sys
import json
import re
import requests
import fal_client
from dotenv import load_dotenv
import costs

load_dotenv()

if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

MODEL = "fal-ai/nano-banana-pro"   # top-tier photorealistic people
OUT_DIR = "output"

# Same cinematic look added to EVERY character so the whole show matches.
# This puts the character INSIDE a real setting (not a plain studio portrait),
# so the talking clips feel like a scene from a TV drama. We keep a medium shot
# (waist up) so the face stays clear enough for good lip-sync.
# NOTE: avoid "film still"/widescreen cues — they make the model produce a
# landscape image even when we ask for 9:16. We push hard for a TALL vertical
# portrait so the character comes out upright in a 9:16 frame.
STYLE = (
    "Cinematic vertical portrait photo, tall 9:16 aspect ratio, portrait "
    "orientation, subject standing upright and centered. In the visual style of "
    "a prestige legal TV drama. The character is inside a modern glass-walled law "
    "office at night with a blurred city skyline behind them. Moody dramatic "
    "lighting, shallow depth of field, photorealistic. Waist-up framing, face "
    "clearly visible."
)


def slug(name: str) -> str:
    """Turn a name like 'Nisha Lark' into a safe file name like 'nisha_lark'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)   # replace spaces/punctuation with _
    return s.strip("_")


def make_image(prompt: str, out_path: str):
    """Send one prompt to Nano Banana Pro and save the returned image.
    Nano Banana takes an aspect_ratio + resolution (not width/height like FLUX).
    9:16 = vertical for TikTok; 2K keeps the face sharp (same price as 1K)."""
    result = fal_client.subscribe(
        MODEL,
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": "9:16",
            "resolution": "2K",
        },
        with_logs=False,
    )
    url = result["images"][0]["url"]
    with open(out_path, "wb") as f:
        f.write(requests.get(url).content)


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run analyze.py first.")

    with open(analysis_path) as f:
        data = json.load(f)

    characters = data.get("characters", [])
    if not characters:
        sys.exit("No characters found in analysis.json.")

    print(f"Found {len(characters)} characters. Making one image each ...\n")

    total_cost = 0.0

    for c in characters:
        name = c["fictional_name"]
        # Combine the character's own prompt with our shared style.
        prompt = f"{c['image_prompt']} {STYLE}"
        file_name = f"char_{slug(name)}.png"
        out_path = os.path.join(OUT_DIR, file_name)

        print(f"- {name} ({c['role']}) ...")
        make_image(prompt, out_path)
        print(f"  saved {out_path}")

        total_cost += costs.NANO_BANANA_PRO_PER_IMAGE
        # Write the image file name back into this character, so everything
        # lives in one place (analysis.json). Next steps read it from here.
        c["file"] = file_name

    # Save the whole analysis file back, now with the image files included.
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the image file of each character.")
    costs.show(f"{len(characters)} character images", total_cost)


if __name__ == "__main__":
    main()
