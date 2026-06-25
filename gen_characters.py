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
Cost:   $0.15 per named character image (Nano Banana Pro).
        $0.025 per anonymous character (cheap FLUX silhouette + a name label).
"""
import os
import sys
import json
import re
import requests
import fal_client
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont   # used to stamp the "Person A" label
import costs

load_dotenv()

if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

MODEL = "fal-ai/nano-banana-pro"   # top-tier photorealistic people
SILHOUETTE_MODEL = "fal-ai/flux/dev"   # cheap model for the anonymous silhouette
OUT_DIR = "output"

# Same cinematic look added to EVERY character so the whole show matches.
# This puts the character INSIDE a real setting (not a plain studio portrait),
# so the talking clips feel like a scene from a TV drama. We keep a medium shot
# (waist up) so the face stays clear enough for good lip-sync.
# NOTE: avoid "film still"/widescreen cues — they make the model produce a
# landscape image even when we ask for 9:16. We push hard for a TALL vertical
# portrait so the character comes out upright in a 9:16 frame.
# POSE: keep the person FRONT-FACING. The model kept turning the whole body
# sideways (shoulders in near-profile) and only swinging the head back, which
# looks off and hurts the talking-avatar lip-sync. So we now lock the SHOULDERS
# AND CHEST toward the camera and allow only a small turn — a subtle three-
# quarter at most. Explicitly forbid a side profile / sideways body.
STYLE = (
    "Cinematic vertical portrait photo, tall 9:16 aspect ratio, portrait "
    "orientation, subject standing upright and centered. The person FACES THE "
    "CAMERA in a front-facing medium shot: shoulders and chest squared toward "
    "the camera, with only a subtle turn of the body for a natural, relaxed look "
    "(a slight three-quarter at most, no more). The head faces forward into the "
    "lens with BOTH eyes clearly visible. Do NOT show a side profile and do NOT "
    "turn the body sideways. In the visual style of a prestige legal TV drama. "
    "The character is inside a modern glass-walled law office at night with a "
    "blurred city skyline behind them. Moody dramatic lighting, shallow depth of "
    "field, photorealistic. Waist-up framing, face clearly visible and facing "
    "the camera."
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


def make_silhouette(out_path: str, gender: str):
    """For ANONYMOUS people (Person A/B) we don't invent a face. Instead we make
    a cinematic backlit silhouette — a featureless, unidentifiable figure — the
    same "protected witness" look used in real legal/TV coverage. We use the
    cheap FLUX model ($0.025) because no facial detail is needed.
    gender just shapes the body outline (woman/man/person); the face stays dark."""
    who = {"female": "a woman", "male": "a man"}.get(gender, "a person")
    result = fal_client.subscribe(
        SILHOUETTE_MODEL,
        arguments={
            "prompt": (
                f"Cinematic backlit silhouette of {who}, completely featureless "
                f"and unidentifiable, face and body in deep shadow, standing "
                f"against a bright glass office window with a blurred city skyline "
                f"at night. Dark moody prestige legal-drama lighting, "
                f"photorealistic, tall vertical 9:16 portrait. No visible facial "
                f"features, anonymous."
            ),
            "image_size": {"width": 720, "height": 1280},
            "num_images": 1,
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "enable_safety_checker": True,
        },
        with_logs=False,
    )
    url = result["images"][0]["url"]
    with open(out_path, "wb") as f:
        f.write(requests.get(url).content)


def add_label(image_path: str, text: str):
    """Burn a TV-style lower-third label (e.g. "JUNIOR ASSOCIATE") onto the
    silhouette, so the viewer instantly reads it as an anonymized person. Done
    with Pillow because the local ffmpeg has no drawtext filter."""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    label = text.upper()

    # Find a usable bold system font file (macOS paths).
    font_path = None
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if os.path.exists(path):
            font_path = path
            break

    # Pick the biggest font size whose label still fits within the frame width
    # (with margins), so longer role labels never run off the edge.
    max_width = int(w * 0.82)
    size = int(w * 0.085)
    if font_path:
        font = ImageFont.truetype(font_path, size)
        while size > 12:
            bbox = draw.textbbox((0, 0), label, font=font)
            if bbox[2] - bbox[0] <= max_width:
                break
            size -= 2
            font = ImageFont.truetype(font_path, size)
    else:
        font = ImageFont.load_default()   # no truetype available; best effort

    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) // 2
    y = int(h * 0.80)           # sit it in the lower third
    pad = int(w * 0.035)

    # Dark bar behind the text (chyron) so the white label always reads.
    draw.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], fill=(0, 0, 0, 170))
    # Offset by the bbox origin so the text sits exactly inside the bar.
    draw.text((x - bbox[0], y - bbox[1]), label, font=font, fill=(255, 255, 255, 255))
    img.save(image_path)


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
        file_name = f"char_{slug(name)}.png"
        out_path = os.path.join(OUT_DIR, file_name)

        if c.get("anonymous"):
            # Anonymous person (Person A/B): no real face. Make a cheap cinematic
            # silhouette and stamp their label on it. Reused every time they
            # appear, so they stay consistent like the real characters.
            print(f"- {name} (anonymous, {c['role']}) ...")
            make_silhouette(out_path, str(c.get("gender", "")).strip().lower())
            add_label(out_path, name)               # burn "PERSON A" onto it
            total_cost += costs.FLUX_DEV_PER_IMAGE   # silhouette uses cheap FLUX
        else:
            # Named character: full photorealistic portrait (Nano Banana Pro).
            prompt = f"{c['image_prompt']} {STYLE}"
            print(f"- {name} ({c['role']}) ...")
            make_image(prompt, out_path)
            total_cost += costs.NANO_BANANA_PRO_PER_IMAGE

        print(f"  saved {out_path}")
        # Write the image file name back into this character, so everything
        # lives in one place (analysis.json). Next steps read it from here.
        c["file"] = file_name

    # Record this step's real cost for the end-of-pipeline summary, naming the
    # model used for each kind of image (portraits vs anonymous silhouettes).
    named = sum(1 for c in characters if not c.get("anonymous"))
    anon = sum(1 for c in characters if c.get("anonymous"))
    parts = []
    if named:
        parts.append(f"Nano Banana Pro x{named} portrait")
    if anon:
        parts.append(f"FLUX dev x{anon} silhouette")
    costs.record(data, "images", "Character images - " + " + ".join(parts), total_cost)

    # Save the whole analysis file back, now with the image files included.
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the image file of each character.")
    costs.show(f"{len(characters)} character images", total_cost)


if __name__ == "__main__":
    main()
