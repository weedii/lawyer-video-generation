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
Cost:   $0.15 per character image (Nano Banana Pro) — every character, named or
        hidden-identity, gets one real locked portrait.
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

# Every character is rendered ONCE as a neutral, multi-angle reference sheet, then
# reused as the identity anchor when composing each scene. Two problems this fixes:
#
#  1) LOCATION BLEED. The old portrait baked the person into a moody night law
#     office with a blue skyline. When that portrait was fed back as a reference to
#     build a DIFFERENT scene, its baked-in look bled through — a daytime courtroom
#     came out blue. A reference has to carry the PERSON, not a place or a colour of
#     light, so this sheet is a plain grey studio with flat, even lighting.
#  2) SINGLE ANGLE. One front photo gives the compositor nothing to work from when a
#     scene needs the person in profile or three-quarter, so the face drifts into
#     someone else. Showing the same person from several sides (plus matching face
#     close-ups) holds identity across whatever angle a scene calls for.
CHAR_SHEET = (
    "A professional character reference sheet of ONE person, photorealistic, in the "
    "restrained look of a prestige legal TV drama. Lay it out as four columns and "
    "two rows — eight shots of the SAME person. Top row: four FULL-BODY views head "
    "to toe (front, side profile, three-quarter, back), nothing cropped at the head, "
    "knees or feet. Bottom row: four matching CLOSE-UPS of the face (front, three-"
    "quarter, profile, slight upward angle) with both eyes clear. Identical face, "
    "hair, build and wardrobe in every shot. Flat, even, neutral studio lighting on "
    "a plain light-grey seamless background, no props and no scenery. Sharp facial "
    "detail."
)


def slug(name: str) -> str:
    """Turn a name like 'Nisha Lark' into a safe file name like 'nisha_lark'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)   # replace spaces/punctuation with _
    return s.strip("_")


def make_image(prompt: str, out_path: str, aspect_ratio: str = "16:9"):
    """Send one prompt to Nano Banana Pro and save the returned image.
    Nano Banana takes an aspect_ratio + resolution (not width/height like FLUX).
    The reference sheet defaults to 16:9 so the four-column, two-row grid of eight
    shots has room to breathe — a 9:16 frame would squeeze eight views into a thin
    column and lose the facial detail we need for identity. 2K keeps faces sharp
    (same price as 1K)."""
    result = fal_client.subscribe(
        MODEL,
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
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

    # Only draw a face for characters who ACTUALLY appear in the script — analyze
    # often invents extra people (a coroner, a witness) the script never uses, and
    # a portrait for someone who never appears is $0.15 wasted. This is why the
    # pipeline writes the script BEFORE this step. Used = anyone listed in a scene,
    # plus the first two named leads (they fill the narration establishing shots).
    scenes = (data.get("script") or {}).get("scenes", [])
    # "used" = anyone who SPEAKS (characters) OR is merely ON SCREEN (onscreen) in
    # any scene — silent reactors are visible too, so they also need a real face.
    used = {n for s in scenes for n in s.get("characters", [])}
    used.update(n for s in scenes for n in s.get("onscreen", []))
    used.update([c["fictional_name"] for c in characters if not c.get("anonymous")][:2])
    if not scenes:                      # script not written yet (standalone run) -> do all
        used = {c["fictional_name"] for c in characters}

    to_make = [c for c in characters if c["fictional_name"] in used]
    print(f"{len(characters)} characters invented; {len(to_make)} used in the "
          f"script. Making a portrait for each used one ...\n")

    total_cost = 0.0

    for c in characters:
        name = c["fictional_name"]
        if name not in used:
            # Not in the script — skip it and save the render.
            print(f"- {name} ({c['role']}) — not in the script, skipped (saved $0.15)")
            continue
        file_name = f"char_{slug(name)}.png"
        out_path = os.path.join(OUT_DIR, file_name)

        # EVERY used character gets a real, photorealistic locked reference sheet
        # (Nano Banana Pro) — including the hidden-identity people. They are real
        # actors on screen, not shadows; the only difference is they are referred to
        # by role, not a real name. The character's own appearance description leads;
        # CHAR_SHEET forces the neutral, multi-angle sheet layout on top of it.
        who = f"{name} ({c['role']}{', by role' if c.get('anonymous') else ''})"
        prompt = f"{c['image_prompt']} {CHAR_SHEET}"
        print(f"- {who} ...")
        make_image(prompt, out_path)
        total_cost += costs.NANO_BANANA_PRO_PER_IMAGE

        print(f"  saved {out_path}")
        # Write the image file name back into this character, so everything
        # lives in one place (analysis.json). Next steps read it from here.
        c["file"] = file_name

    # Record this step's real cost for the end-of-pipeline summary.
    costs.record(data, "images",
                 f"Character portraits - Nano Banana Pro x{len(to_make)}",
                 total_cost)

    # Save the whole analysis file back, now with the image files included.
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the image file of each character.")
    costs.show(f"{len(to_make)} character images", total_cost)


if __name__ == "__main__":
    main()
