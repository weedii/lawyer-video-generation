"""STAGE 2 - STEP 3: Make a video clip for every line.

For each DIALOGUE line: take the speaking character's image + that line's voice
and make a realistic talking clip (Kling AI Avatar v2).

For each NARRATION line (the intro hook + the cliffhanger): there is no talking
face, so instead we show a cinematic ESTABLISHING SHOT of the scene's setting
with the narrator's voice over it (a cheap static-image video, no Kling).

Usage:
    python talking_clips.py

Reads:  output/analysis.json   (characters with images + script lines with audio)
Output: output/clip_01_<name>.mp4, ...   (one per line, in order)
        It also writes each clip's file name back INTO analysis.json.
Cost:   $0.056 per second of Kling video, plus $0.025 once for the
        establishing image (only if the script has narration).
"""
import os
import sys
import json
import re
import time
import subprocess
import requests
import fal_client
from dotenv import load_dotenv
import costs

load_dotenv()

if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

AVATAR_MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"   # talking dialogue clips
FLUX_MODEL = "fal-ai/flux/dev"                       # for the establishing shot
OUT_DIR = "output"
ESTABLISHING_FILE = "establishing.png"               # reused for all narration


def slug(name: str) -> str:
    """Turn 'Leo Finnegan' into 'leo_finnegan' for safe file names."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def make_talking_clip(image_url: str, audio_url: str, out_path: str) -> float:
    """Send one image + audio to Kling and save the talking video.
    Returns the video duration in seconds (used for the cost).
    Retries a few times: fal sometimes throws a transient 'could not download
    the file' (422) error that succeeds on a second try."""
    last_err = None
    for attempt in range(1, 4):
        try:
            result = fal_client.subscribe(
                AVATAR_MODEL,
                arguments={"image_url": image_url, "audio_url": audio_url},
                with_logs=False,
            )
            url = result["video"]["url"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return float(result.get("duration", 0))
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


def make_establishing_image(setting: str, out_path: str):
    """Generate ONE cinematic establishing shot of the setting (no people),
    used as the backdrop for the narrator's voiceover lines."""
    result = fal_client.subscribe(
        FLUX_MODEL,
        arguments={
            "prompt": (
                f"Cinematic establishing shot of {setting}. No people. Moody "
                f"dramatic lighting, Suits/Billions TV-drama style, photorealistic, "
                f"35mm film look. Vertical 9:16."
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


def make_narration_clip(image_path: str, audio_path: str, out_path: str):
    """Make a static video: the establishing image held for the length of the
    narrator's audio. Done locally with ffmpeg, so it is free."""
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,   # hold the image
        "-i", audio_path,                  # narrator voice
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-c:a", "aac", "-ar", "44100",
        "-shortest",                       # stop when the audio ends
        out_path,
    ], check=True, capture_output=True)


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

    uploaded_image = {}        # cache: character name -> uploaded image URL

    lines = script["lines"]
    made = 0
    total_seconds = 0.0        # Kling seconds (for cost)
    image_cost = 0.0           # establishing image cost (if any)
    print(f"Making clips for {len(lines)} lines ...")

    for i, ln in enumerate(lines, 1):
        name = ln["character"]
        audio_file = ln.get("audio")
        if not audio_file:
            print(f"  [{i}] WARNING: no audio for '{name}', skipping.")
            continue
        audio_path = os.path.join(OUT_DIR, audio_file)

        # --- NARRATION line: static establishing shot + voiceover ---
        if ln.get("type") == "narration" or name == "Narrator":
            est_path = os.path.join(OUT_DIR, ESTABLISHING_FILE)
            # Generate the establishing image once, then reuse it.
            if not os.path.exists(est_path):
                print("  generating establishing shot ...")
                make_establishing_image(script.get("setting", "a law office"), est_path)
                image_cost += costs.FLUX_DEV_PER_IMAGE

            file_name = f"clip_{i:02d}_narrator.mp4"
            out_path = os.path.join(OUT_DIR, file_name)
            make_narration_clip(est_path, audio_path, out_path)
            ln["clip"] = file_name
            made += 1
            print(f"  [{i}] Narrator: saved {file_name} (establishing shot)")
            continue

        # --- DIALOGUE line: Kling talking face ---
        img_file = image_file.get(name)
        if not img_file:
            print(f"  [{i}] WARNING: no image for '{name}', skipping.")
            continue

        file_name = f"clip_{i:02d}_{slug(name)}.mp4"
        out_path = os.path.join(OUT_DIR, file_name)
        img_path = os.path.join(OUT_DIR, img_file)
        # Upload the character image once, the audio every line.
        if name not in uploaded_image:
            uploaded_image[name] = fal_client.upload_file(img_path)
        audio_url = fal_client.upload_file(audio_path)

        seconds = make_talking_clip(uploaded_image[name], audio_url, out_path)
        ln["clip"] = file_name
        made += 1
        total_seconds += seconds
        print(f"  [{i}] {name}: saved {file_name} ({seconds:.1f}s)")

    # Save the clip file names back into analysis.json (one place for all).
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the clips.")
    costs.show(
        f"{made} clips ({total_seconds:.1f}s Kling + establishing image)",
        total_seconds * costs.KLING_AVATAR_PER_SEC + image_cost,
    )


if __name__ == "__main__":
    main()
