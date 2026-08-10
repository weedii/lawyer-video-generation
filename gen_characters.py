"""STEP C: Make one locked portrait for each character the SCRIPT actually uses.

It reads the analysis file (from analyze.py + scene_writer.py), and for every character
who appears in a scene (speaking or silent on screen), plus the protagonist who narrates,
generates one vertical portrait with fal.ai. Characters analyze invented but the script
never uses are skipped, so we don't pay for faces that never appear. It also saves the
image file name back into analysis.json so the next steps know which picture is whose.

Usage:
    python gen_characters.py

Reads:  output/analysis.json    (from analyze.py + scene_writer.py)
Output: output/char_<name>.png  (one image per USED character)
        Each character also gets a "file" field in analysis.json, so everything
        stays in one place.
Cost:   $0.08 per character image (Nano Banana 2, 1K) — named or hidden-identity alike.
"""
import os
import sys
import json
import re
import time
import requests
import fal_client
from dotenv import load_dotenv
import costs

load_dotenv()

if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

# Nano Banana 2 (fal) = Google Gemini 3.1 Flash Image. These portraits are the locked
# identity reference reused everywhere downstream, so body errors here poison every scene
# the character appears in — which is why we moved up from non-pro ($0.039), whose people
# came out with extra limbs and drifting gender. We run the 1K DEFAULT: no "resolution"
# argument is sent, so we pay the $0.08 base rate (2K would be 1.5x for detail that
# Seedance at 720p throws away anyway).
MODEL = "fal-ai/nano-banana-2"      # photorealistic people, 1K default
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
#  3) BURNT-IN TEXT. Calling it a "reference sheet" made Nano render a real titled
#     document — a "CHARACTER REFERENCE: …" header bar across the top. That is not just
#     ugly: this sheet is fed as a REFERENCE into every scene compose, so any text on it
#     can bleed into the scenes. So we describe a plain multi-angle STUDY (not a titled
#     sheet) and forbid ALL text explicitly.
CHAR_SHEET = (
    "A professional multi-angle character study of ONE person, photorealistic, in the "
    "restrained look of a prestige legal TV drama. Lay it out as four columns and "
    "two rows — eight shots of the SAME person. Top row: four FULL-BODY views head "
    "to toe (front, side profile, three-quarter, back), nothing cropped at the head, "
    "knees or feet. Bottom row: four matching CLOSE-UPS of the face (front, three-"
    "quarter, profile, slight upward angle) with both eyes clear. Identical face, "
    "hair, build and wardrobe in every shot. Flat, even, neutral studio lighting on "
    "a plain light-grey seamless background, no props and no scenery. Sharp facial "
    "detail. NO TEXT of any kind anywhere in the image: no title, no header or caption "
    "bar, no label, no name, no words, no letters, no numbers, no logo, no watermark — "
    "a purely photographic image with nothing written on it."
)


def slug(name: str) -> str:
    """Turn a name like 'Nisha Lark' into a safe file name like 'nisha_lark'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)   # replace spaces/punctuation with _
    return s.strip("_")


def make_image(prompt: str, out_path: str, aspect_ratio: str = "16:9"):
    """Send one prompt to Nano Banana 2 and save the returned image.
    Nano Banana takes an aspect_ratio (not width/height like FLUX). The reference sheet
    defaults to 16:9 so the four-column, two-row grid of eight shots has room to breathe —
    a 9:16 frame would squeeze eight views into a thin column and lose facial detail.
    We deliberately send NO "resolution": 1K is the default and the cheapest tier, and
    passing "2K" would cost 1.5x for sharpness the 720p video model discards."""
    result = fal_client.subscribe(
        MODEL,
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
        },
        with_logs=False,
    )
    url = result["images"][0]["url"]
    _download(url, out_path)


def _download(url: str, out_path: str, tries: int = 4):
    """Download a generated image, retrying transient network/SSL errors. The image is
    already generated and PAID before this runs, so a flaky download (a dropped or
    corrupted TLS packet) must never crash the run or waste the render. We retry the SAME
    url — no re-generation, so no extra cost — with a timeout, and only fetch the bytes
    fully BEFORE opening the file, so a mid-transfer failure can't leave a half-written or
    empty .png behind. Gives up (raising) only after several failed attempts."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            data = r.content                     # read fully first; raises here on a bad packet
            with open(out_path, "wb") as f:      # only touch the file once we have all bytes
                f.write(data)
            return
        except Exception as e:
            last = e
            print(f"    image download attempt {attempt}/{tries} failed "
                  f"({type(e).__name__}); retrying in 3s ...")
            time.sleep(3)
    raise RuntimeError(f"could not download image after {tries} tries: {last}")


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
    # a portrait for someone who never appears is $0.08 wasted. This is why the
    # pipeline writes the script BEFORE this step.
    scenes = (data.get("script") or {}).get("scenes", [])
    # "used" = anyone who SPEAKS (characters) OR is merely ON SCREEN (onscreen) in
    # any scene — silent reactors are visible too, so they also need a real face.
    used = {n for s in scenes for n in s.get("characters", [])}
    used.update(n for s in scenes for n in s.get("onscreen", []))
    # Also guarantee the protagonist (first non-anonymous character), who narrates the whole
    # video. Only the first one — an earlier version added the first TWO for old "establishing
    # shots", which paid for the 2nd character even when the script never used them.
    used.update([c["fictional_name"] for c in characters if not c.get("anonymous")][:1])
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
            print(f"- {name} ({c['role']}) — not in the script, skipped "
                  f"(saved ${costs.NANO_BANANA_2_PER_IMAGE})")
            continue
        file_name = f"char_{slug(name)}.png"
        out_path = os.path.join(OUT_DIR, file_name)

        # Resume guard: a portrait already on disk (e.g. from a run that crashed partway,
        # like a dropped download) is reused for free — record its file and move on, so a
        # re-run never re-pays Nano for a face we already have. We require a non-empty file:
        # a crash mid-download could have left a 0-byte stub, and that must be regenerated,
        # not mistaken for a finished portrait.
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"- {name} ({c['role']}) — reused, already on disk ($0)")
            c["file"] = file_name
            continue

        # EVERY used character gets a real, photorealistic locked reference sheet
        # (Nano Banana 2) — including the hidden-identity people. They are real
        # actors on screen, not shadows; the only difference is they are referred to
        # by role, not a real name. The character's own appearance description leads;
        # CHAR_SHEET forces the neutral, multi-angle sheet layout on top of it.
        who = f"{name} ({c['role']}{', by role' if c.get('anonymous') else ''})"
        prompt = f"{c['image_prompt']} {CHAR_SHEET}"
        print(f"- {who} ...")
        make_image(prompt, out_path)
        total_cost += costs.NANO_BANANA_2_PER_IMAGE

        print(f"  saved {out_path}")
        # Write the image file name back into this character, so everything
        # lives in one place (analysis.json). Next steps read it from here.
        c["file"] = file_name

    # Record this step's real cost for the end-of-pipeline summary.
    costs.record(data, "images",
                 f"Character portraits - Nano Banana 2 (1K) x{len(to_make)}",
                 total_cost)

    # Save the whole analysis file back, now with the image files included.
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the image file of each character.")
    costs.show(f"{len(to_make)} character images", total_cost)


if __name__ == "__main__":
    main()
