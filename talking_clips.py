"""STAGE 2 - STEP 3: Make a video clip for every line.

For each DIALOGUE line by a NAMED character: take their image + that line's
voice and make a clip where they actually MOVE, gesture and act while speaking
(OmniHuman 1.5). Set TALKING_MODEL = "kling" for the cheaper, near-static
talking-head instead.

For each NARRATION line (the intro hook + the cliffhanger): there is no talking
face, so instead we show a cinematic ESTABLISHING SHOT of the scene's setting
with the narrator's voice over it (a cheap static-image video, no Kling).

For each ANONYMOUS speaker (Person A/B — people the story doesn't name): there
is no face to lip-sync, so we hold their silhouette image over their voice (a
static-image video, free, no Kling), keeping the conversation back-and-forth.

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
from PIL import Image          # to shrink the portrait before sending it inline
from dotenv import load_dotenv
import costs

load_dotenv()

if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

# Which model animates the NAMED talking characters:
#   "omnihuman" -> OmniHuman 1.5: the character actually MOVES, gestures and acts
#                  with body language + camera motion while talking ($0.16/sec).
#                  This is what makes the scene feel alive (the boss's request).
#   "kling"     -> Kling AI Avatar v2: cheaper ($0.0562/sec) but barely moves
#                  (just the mouth + tiny head). Kept as a budget fallback.
TALKING_MODEL = "omnihuman"

OMNIHUMAN_MODEL = "fal-ai/bytedance/omnihuman/v1.5"          # moving, acting talker
AVATAR_MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"   # cheap static talker
FLUX_MODEL = "fal-ai/flux/dev"                       # for the establishing shot
OUT_DIR = "output"
ESTABLISHING_FILE = "establishing.png"               # reused for all narration


def slug(name: str) -> str:
    """Turn 'Leo Finnegan' into 'leo_finnegan' for safe file names."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def make_talking_clip(image_url: str, audio_url: str, out_path: str,
                      prompt: str = "") -> float:
    """Send one image + audio to Kling and save the talking video.
    Returns the video duration in seconds (used for the cost).
    prompt: Kling's optional text field. It only guides SUBTLE aspects of the
    animation (no camera moves, no strong emotion), so we feed the line's
    emotion here hoping for a small expression nudge.
    Retries a few times: fal sometimes throws a transient 'could not download
    the file' (422) error that succeeds on a second try."""
    args = {"image_url": image_url, "audio_url": audio_url}
    if prompt:
        args["prompt"] = prompt   # subtle expression hint only
    last_err = None
    for attempt in range(1, 4):
        try:
            result = fal_client.subscribe(
                AVATAR_MODEL,
                arguments=args,
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


def motion_prompt(ln: dict, setting: str) -> str:
    """Build the scene direction for OmniHuman so the character ACTS, not just
    lip-syncs. OmniHuman reads a prompt shaped like:
    [camera] + [mood] + [what the body does] + [setting]."""
    emotion = (ln.get("emotion") or "").strip()
    camera = (ln.get("camera") or "").strip()
    parts = []
    if camera:
        parts.append(camera)
    if emotion:
        parts.append(f"{emotion} mood")
    parts.append(
        "the character moves and gestures naturally while speaking, with "
        "expressive body language, shifting posture and hand movements, "
        "alive and engaged"
    )
    parts.append(f"set in {setting}, cinematic prestige legal drama, photorealistic")
    return ". ".join(parts) + "."


def small_image_data_uri(path: str) -> str:
    """Shrink the portrait to ~720x1280 and return it as an inline data: URI.
    OmniHuman often can't DOWNLOAD an uploaded fal URL (file_download_error), so
    we send the image inline instead; shrinking keeps that payload small."""
    tmp = path + ".omni.jpg"
    img = Image.open(path).convert("RGB")
    if img.height > 1280:
        img = img.resize((max(1, round(img.width * 1280 / img.height)), 1280))
    img.save(tmp, "JPEG", quality=90)
    uri = fal_client.encode_file(tmp)
    os.remove(tmp)
    return uri


def make_omnihuman_clip(image_path: str, audio_path: str, prompt: str,
                        out_path: str) -> float:
    """Send one image + audio (+ scene prompt) to OmniHuman 1.5 and save the
    video. Unlike Kling, the character actually moves and acts. The output
    length matches the audio. Returns the duration in seconds (for the cost).
    We send the image INLINE (a data URI — OmniHuman can't download a big
    uploaded image) but upload the small audio as a normal URL (OmniHuman won't
    accept audio as a data URI). Retries a few times on transient fal errors."""
    image_uri = small_image_data_uri(image_path)
    audio_uri = fal_client.upload_file(audio_path)
    last_err = None
    for attempt in range(1, 4):
        try:
            result = fal_client.subscribe(
                OMNIHUMAN_MODEL,
                arguments={
                    "image_url": image_uri,
                    "audio_url": audio_uri,
                    "prompt": prompt,
                },
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


def ensure_establishing(setting: str) -> tuple[str, float]:
    """Make the establishing image once (if it's missing) and return its path
    plus the cost added (0.0 if it already existed). Used for narrator lines and
    as a safe fallback for any speaker we have no face for."""
    est_path = os.path.join(OUT_DIR, ESTABLISHING_FILE)
    if os.path.exists(est_path):
        return est_path, 0.0
    print("  generating establishing shot ...")
    make_establishing_image(setting, est_path)
    return est_path, costs.FLUX_DEV_PER_IMAGE


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    script = data.get("script")
    if not script or not script.get("lines"):
        sys.exit("No script found. Run scene_writer.py first.")

    # Map each character name to their image file, and note who is anonymous.
    characters = data.get("characters", [])
    image_file = {c["fictional_name"]: c.get("file") for c in characters}
    anonymous_names = {c["fictional_name"] for c in characters if c.get("anonymous")}

    uploaded_image = {}        # cache: character name -> uploaded image URL
    setting = script.get("setting", "a law office")

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
            est_path, added = ensure_establishing(setting)
            image_cost += added
            file_name = f"clip_{i:02d}_narrator.mp4"
            out_path = os.path.join(OUT_DIR, file_name)
            make_narration_clip(est_path, audio_path, out_path)
            ln["clip"] = file_name
            made += 1
            print(f"  [{i}] Narrator: saved {file_name} (establishing shot)")
            continue

        # --- ANONYMOUS speaker (Person A/B): no face to lip-sync. Hold their
        # silhouette image over their voice (free ffmpeg), like a narration shot
        # but with the silhouette. This keeps the back-and-forth conversation. ---
        if name in anonymous_names:
            sil_file = image_file.get(name)
            file_name = f"clip_{i:02d}_{slug(name)}.mp4"
            out_path = os.path.join(OUT_DIR, file_name)
            if sil_file:
                make_narration_clip(os.path.join(OUT_DIR, sil_file), audio_path, out_path)
            else:
                # Silhouette missing for some reason — fall back to the
                # establishing shot so the line is still heard, never dropped.
                est_path, added = ensure_establishing(setting)
                image_cost += added
                make_narration_clip(est_path, audio_path, out_path)
            ln["clip"] = file_name
            made += 1
            print(f"  [{i}] {name}: saved {file_name} (silhouette)")
            continue

        # --- DIALOGUE line: Kling talking face ---
        img_file = image_file.get(name)
        if not img_file:
            # Named speaker but no face image (unexpected). Don't drop the line —
            # render it over the establishing shot so the scene stays complete.
            est_path, added = ensure_establishing(setting)
            image_cost += added
            file_name = f"clip_{i:02d}_{slug(name)}.mp4"
            out_path = os.path.join(OUT_DIR, file_name)
            make_narration_clip(est_path, audio_path, out_path)
            ln["clip"] = file_name
            made += 1
            print(f"  [{i}] {name}: saved {file_name} (no image, used establishing shot)")
            continue

        file_name = f"clip_{i:02d}_{slug(name)}.mp4"
        out_path = os.path.join(OUT_DIR, file_name)
        img_path = os.path.join(OUT_DIR, img_file)

        # Animate the talker with the chosen model.
        if TALKING_MODEL == "omnihuman":
            # OmniHuman: send image + audio inline (data URIs); the character
            # moves and acts from the scene prompt.
            seconds = make_omnihuman_clip(
                img_path, audio_path, motion_prompt(ln, setting), out_path)
        else:
            # Kling: upload the image (once) + audio, feed emotion as a small nudge.
            if name not in uploaded_image:
                uploaded_image[name] = fal_client.upload_file(img_path)
            audio_url = fal_client.upload_file(audio_path)
            emotion = (ln.get("emotion") or "").strip()
            kp = f"{emotion} facial expression, subtle natural movement" if emotion else ""
            seconds = make_talking_clip(uploaded_image[name], audio_url, out_path, kp)
        ln["clip"] = file_name
        made += 1
        total_seconds += seconds
        print(f"  [{i}] {name}: saved {file_name} ({seconds:.1f}s)")

    # Cost depends on which talking model ran.
    if TALKING_MODEL == "omnihuman":
        talking_rate, model_label = costs.OMNIHUMAN_PER_SEC, "OmniHuman 1.5"
    else:
        talking_rate, model_label = costs.KLING_AVATAR_PER_SEC, "Kling AI Avatar v2 std"
    clip_cost = total_seconds * talking_rate + image_cost

    # Record this step's real cost for the end-of-pipeline summary.
    est_note = " + FLUX dev establishing shot" if image_cost else ""
    costs.record(data, "clips",
                 f"Talking clips - {model_label} ({total_seconds:.1f}s){est_note}",
                 clip_cost)

    # Save the clip file names back into analysis.json (one place for all).
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the clips.")
    costs.show(
        f"{made} clips ({total_seconds:.1f}s {model_label} + establishing image)",
        clip_cost,
    )


if __name__ == "__main__":
    main()
