"""STAGE 2 - STEP 3: Make a talking clip for every line.

For each line in the scene script, it takes the speaking character's image and
that line's voice, and makes a lip-synced talking video (SadTalker). One clip
per line, in order, ready to be stitched together later.

Usage:
    python talking_clips.py

Reads:  output/analysis.json   (needs characters with images + script lines with audio)
Output: output/clip_01_<name>.mp4, clip_02_<name>.mp4, ...   (one per line)
        It also writes each clip's file name back INTO analysis.json.
Cost:   $0.056 per second of video (Kling AI Avatar v2).
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

MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"
OUT_DIR = "output"


def slug(name: str) -> str:
    """Turn 'Leo Finnegan' into 'leo_finnegan' for safe file names."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def make_clip(image_url: str, audio_url: str, out_path: str) -> float:
    """Send one image + audio to Kling and save the talking video.
    Returns the video duration in seconds (used for the cost)."""
    result = fal_client.subscribe(
        MODEL,
        arguments={"image_url": image_url, "audio_url": audio_url},
        with_logs=False,
    )
    url = result["video"]["url"]
    with open(out_path, "wb") as f:
        f.write(requests.get(url).content)
    return float(result.get("duration", 0))


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    script = data.get("script")
    if not script or not script.get("lines"):
        sys.exit("No script found. Run scene_writer.py first.")

    # Map each character name to their image file.
    image_file = {c["fictional_name"]: c.get("file") for c in data.get("characters", [])}

    # We upload each character's image only once, then reuse the URL.
    uploaded_image = {}

    lines = script["lines"]
    made = 0
    total_seconds = 0.0
    print(f"Making talking clips for {len(lines)} lines ...")

    for i, ln in enumerate(lines, 1):
        name = ln["character"]
        audio_file = ln.get("audio")
        img_file = image_file.get(name)

        # Skip safely if something is missing for this line.
        if not audio_file or not img_file:
            print(f"  [{i}] WARNING: missing image or audio for '{name}', skipping.")
            continue

        img_path = os.path.join(OUT_DIR, img_file)
        audio_path = os.path.join(OUT_DIR, audio_file)

        # Upload the image once per character, the audio every line.
        if name not in uploaded_image:
            uploaded_image[name] = fal_client.upload_file(img_path)
        audio_url = fal_client.upload_file(audio_path)

        file_name = f"clip_{i:02d}_{slug(name)}.mp4"
        out_path = os.path.join(OUT_DIR, file_name)
        seconds = make_clip(uploaded_image[name], audio_url, out_path)

        ln["clip"] = file_name   # remember the clip for this line
        made += 1
        total_seconds += seconds
        print(f"  [{i}] {name}: saved {file_name} ({seconds:.1f}s)")

    # Save the clip file names back into analysis.json (one place for all).
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the talking clips.")
    costs.show(
        f"{made} talking clips ({total_seconds:.1f}s total)",
        total_seconds * costs.KLING_AVATAR_PER_SEC,
    )


if __name__ == "__main__":
    main()
