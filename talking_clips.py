"""STAGE 2 - STEP 3: Make a video clip for every line.

For each DIALOGUE line by a NAMED character: make the character ACT and talk on
one clip with the COMBO — Seedance animates their photo so they move with their
body (stand up, lean in, gesture), then Sync lip-syncs OUR voice onto that moving
video. The line's action/emotion/camera cues drive the motion. Switch
TALKING_MODEL to "veed" / "kling" / "omnihuman" for the single-model engines.

For each NARRATION line (the intro hook + the cliffhanger): there is no talking
face, so instead we animate a SCENE with real motion — two lawyers in the same
room, one standing up (Seedance 1.5 Pro) — and lay the narrator's voice over it.
This is where the big "actors in a room" motion lives. If Seedance fails we fall
back to a cheap static establishing shot so the line is never lost.

For each ANONYMOUS speaker (Person A/B — people the story doesn't name): there
is no face to lip-sync, so we hold their silhouette image over their voice (a
static-image video, free, no Kling), keeping the conversation back-and-forth.

Usage:
    python talking_clips.py

Reads:  output/analysis.json   (characters with images + script lines with audio)
Output: output/clip_01_<name>.mp4, ...   (one per line, in order)
        It also writes each clip's file name back INTO analysis.json.
Cost:   $0.08 per second of VEED Fabric 480p video, plus the Seedance motion
        beats and a $0.15 two-character scene image (only if the script has
        narration).
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

# HYBRID pipeline (two models, two jobs):
#   - DIALOGUE lines  -> a lip-sync talker (below) makes the character's mouth
#                        match our ElevenLabs voice. Close-up, gentle motion.
#   - NARRATION beats -> Seedance 1.5 Pro animates a SCENE (two lawyers in the
#                        room, real motion like standing up) with the narrator
#                        voice over it. This is where the big motion lives, since
#                        lip-sync models can't do it.
#
# Which model makes the NAMED talking characters:
#   "seedance_sync" -> THE COMBO (default): Seedance animates the photo so the
#                  character ACTS with their body (stands up, leans in, gestures),
#                  then Sync lip-syncs OUR voice onto that moving video. Real
#                  acting + correct lips on the same clip (~$0.038/sec total).
#   "veed"      -> VEED Fabric 1.0: lip-sync only, gentle motion, no acting
#                  ($0.08/sec). Kept as a cheaper-to-run fallback.
#   "kling"     -> Kling AI Avatar v2 std: weakest lip-sync, pads clips. Fallback.
#   "omnihuman" -> OmniHuman 1.5: acting + lip-sync in one model, ~$0.16/sec.
TALKING_MODEL = "seedance_sync"

OMNIHUMAN_MODEL = "fal-ai/bytedance/omnihuman/v1.5"          # moving, acting talker
AVATAR_MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"   # cheap lip-sync talker
VEED_MODEL = "veed/fabric-1.0"                               # lip-sync-only talker
VEED_RESOLUTION = "480p"                                      # 480p (cheap) or 720p (HD)
SEEDANCE_MODEL = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"  # body motion
SYNC_MODEL = "fal-ai/sync-lipsync"                  # lip-syncs our voice onto video
SCENE_IMAGE_MODEL = "fal-ai/nano-banana-pro"         # two characters in one room
FLUX_MODEL = "fal-ai/flux/dev"                       # plain establishing fallback
OUT_DIR = "output"
ESTABLISHING_FILE = "establishing.png"               # plain still fallback
SCENE_FILE = "scene_two.png"                          # two-character scene image
# Seedance takes an integer duration; keep narration motion clips in this range.
SEEDANCE_MIN_SEC = 4
SEEDANCE_MAX_SEC = 12


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


def make_veed_clip(image_url: str, audio_url: str, out_path: str) -> float:
    """Send one image + audio to VEED Fabric 1.0 and save the talking video.
    Better lip-sync than Kling, and the clip length MATCHES the voice (no
    padding). Returns the real duration in seconds (read from the file) for the
    cost. Retries a few times on transient fal errors."""
    last_err = None
    for attempt in range(1, 4):
        try:
            result = fal_client.subscribe(
                VEED_MODEL,
                arguments={
                    "image_url": image_url,
                    "audio_url": audio_url,
                    "resolution": VEED_RESOLUTION,
                },
                with_logs=False,
            )
            url = result["video"]["url"] if isinstance(result.get("video"), dict) \
                else result["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return audio_duration(out_path)   # length matches the voice
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


def make_acting_clip(image_url: str, audio_path: str, prompt: str,
                     out_path: str) -> tuple[float, float]:
    """THE COMBO — two models on ONE clip:
      Stage 1 (Seedance): animate the character photo so they ACT with their body
        (stand up, lean in, gesture) while facing the camera. Silent.
      Stage 2 (Sync): take that moving video + our ElevenLabs voice and redo the
        MOUTH so it matches the words.
    Result: real body acting AND our voice lip-synced on the same clip.
    Returns (seedance_seconds, sync_seconds) for the cost. Each stage retries a
    few times on transient fal errors so the run doesn't die on a hiccup."""
    voice_len = audio_duration(audio_path)
    # Make the motion clip a touch LONGER than the voice so it fully covers it;
    # Sync then trims back to the voice length (sync_mode "cut_off").
    dur = int(max(SEEDANCE_MIN_SEC, min(SEEDANCE_MAX_SEC, round(voice_len + 1))))

    # Stage 1: Seedance body motion (no audio — Sync adds our voice next).
    seedance_url = None
    last_err = None
    for attempt in range(1, 4):
        try:
            r = fal_client.subscribe(
                SEEDANCE_MODEL,
                arguments={
                    "prompt": prompt,
                    "image_url": image_url,
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "duration": dur,
                    "generate_audio": False,
                },
                with_logs=False,
            )
            seedance_url = r["video"]["url"] if isinstance(r.get("video"), dict) \
                else r["video"]
            break
        except Exception as e:
            last_err = e
            print(f"    seedance attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    if not seedance_url:
        raise last_err

    # Stage 2: Sync lip-syncs our voice onto the Seedance video. We pass the
    # Seedance result URL straight in (no re-upload); only the voice is uploaded.
    audio_url = fal_client.upload_file(audio_path)
    last_err = None
    for attempt in range(1, 4):
        try:
            r2 = fal_client.subscribe(
                SYNC_MODEL,
                arguments={
                    "video_url": seedance_url,
                    "audio_url": audio_url,
                    "sync_mode": "cut_off",   # trim the video back to the voice length
                },
                with_logs=False,
            )
            url2 = r2["video"]["url"] if isinstance(r2.get("video"), dict) \
                else r2["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url2).content)
            return float(dur), audio_duration(out_path)
        except Exception as e:
            last_err = e
            print(f"    sync attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


def motion_prompt(ln: dict, setting: str) -> str:
    """Build the scene direction for OmniHuman so the character ACTS, not just
    lip-syncs. OmniHuman reads a prompt shaped like:
    [camera] + [mood] + [what the body does] + [setting]."""
    emotion = (ln.get("emotion") or "").strip()
    camera = (ln.get("camera") or "").strip()
    action = (ln.get("action") or "").strip()
    parts = []
    if camera:
        parts.append(camera)
    if emotion:
        parts.append(f"{emotion} mood")
    if action:
        # The actor's stage direction: make the character perform this action.
        parts.append(f"the character {action} while speaking, expressive body language")
    else:
        parts.append(
            "the character moves and gestures naturally while speaking, with "
            "expressive body language, shifting posture and hand movements"
        )
    parts.append(f"set in {setting}, cinematic prestige legal drama, photorealistic")
    return ". ".join(parts) + "."


def dialogue_acting_prompt(ln: dict, setting: str) -> str:
    """Scene direction for the COMBO dialogue clip (Seedance stage). Turns the
    scene writer's per-line cues into a motion prompt so the character ACTS out
    their line. CRITICAL: the face and mouth must stay toward the camera, or the
    Sync lip-sync (stage 2) has no mouth to fix — so we forbid turning away or
    leaving the frame, while still allowing stand-up / lean-in / hand gestures."""
    emotion = (ln.get("emotion") or "").strip()
    camera = (ln.get("camera") or "").strip()
    action = (ln.get("action") or "").strip()
    parts = [camera or "slow push-in"]
    if emotion:
        parts.append(f"{emotion} mood")
    if action:
        # The actor's stage direction from scene_writer.py.
        parts.append(f"the character {action} while speaking, expressive body language")
    else:
        parts.append("the character gestures and shifts posture while speaking")
    # Keep the mouth visible so stage-2 lip-sync works.
    parts.append("the character stays facing the camera with face and mouth "
                 "clearly visible, does not turn away or leave the frame")
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


# --- Seedance scene motion (for narration beats) ---------------------------

def audio_duration(audio_path: str) -> float:
    """Read the length of an audio file (seconds) with ffprobe, so a narration
    motion clip can be made just long enough to cover the voiceover."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        check=True, capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 5.0


def is_portrait(path: str) -> bool:
    """True if the image is taller than it is wide (a proper 9:16 vertical)."""
    w, h = Image.open(path).size
    return h > w


def make_scene_image(setting: str, out_path: str):
    """Make ONE cinematic image with TWO lawyers in the SAME room (one seated at
    the desk, one standing opposite). This is the still frame Seedance animates
    for the narration beats, so the viewer sees real people in a real place — the
    'actors in a scene' look the boss asked for. Nano Banana Pro, $0.15.

    Nano Banana can drift to LANDSCAPE when the setting sounds wide (e.g. a
    'crowded courtroom'), which ruins a TikTok 9:16 video. So we (1) drop wide
    cues like 'film still', (2) push hard for a tall vertical frame, and (3)
    check the result and regenerate if it still came out landscape."""
    # IMPORTANT lesson baked into this prompt:
    # - Describing a WIDE/horizontal layout ("two people across a desk in a
    #   courtroom") makes the model compose wide and then ROTATE it 90° to fit a
    #   9:16 frame (people end up lying sideways). The pixel size stays portrait,
    #   so an orientation check can't see it.
    # - Fix WITHOUT losing the room: compose the room VERTICALLY using DEPTH and
    #   HEIGHT — the two lawyers in the foreground, the room rising up BEHIND and
    #   ABOVE them — so the natural composition is tall, not wide. We still get
    #   the full environment, just stacked vertically instead of side-to-side.
    base = (
        f"Vertical 9:16 portrait photo, upright, taller than wide. A cinematic "
        f"wide-angle view of {setting}, composed VERTICALLY using depth: in the "
        f"FOREGROUND two male lawyers in sharp suits stand at a large desk — one "
        f"seated, one standing — turned toward each other, tense; BEHIND and "
        f"ABOVE them the room rises to show its full environment (furniture, wood "
        f"panelling, tall windows or high ceiling). The camera is held UPRIGHT: "
        f"the people are upright with heads toward the top of the frame, the room "
        f"filling the space above. Moody dramatic lighting, shallow depth of "
        f"field, prestige legal drama, photorealistic. NOT rotated, NOT sideways, "
        f"NOT landscape."
    )
    for attempt in range(1, 3):
        prompt = base if attempt == 1 else (
            base + " CRITICAL: vertical phone frame, people UPRIGHT with heads at "
            "the top and the room rising above them. Do NOT rotate, tilt, or make "
            "it sideways/landscape.")
        result = fal_client.subscribe(
            SCENE_IMAGE_MODEL,
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
        if is_portrait(out_path):
            return
        print(f"    scene image came out landscape; regenerating (attempt {attempt})")
    print("    WARNING: scene image still not vertical after retries.")


def ensure_scene_image(setting: str) -> tuple[str, float]:
    """Make the two-character scene image once and return its path plus the cost
    added (0.0 if it already existed). Reuses a cached image ONLY if it is a
    proper vertical — a previously cached landscape one is regenerated."""
    scene_path = os.path.join(OUT_DIR, SCENE_FILE)
    if os.path.exists(scene_path) and is_portrait(scene_path):
        return scene_path, 0.0
    print("  generating two-character scene image ...")
    make_scene_image(setting, scene_path)
    return scene_path, costs.NANO_BANANA_PRO_PER_IMAGE


def make_seedance_clip(image_path: str, prompt: str, seconds: float,
                       out_path: str) -> float:
    """Animate the scene image with Seedance 1.5 Pro: real body motion (stand up,
    lean in, gesture) and a camera move. Audio OFF (we add the narrator voice
    after) to halve the cost. Returns the clip duration billed. Retries on
    transient fal errors so the run doesn't die on a hiccup."""
    dur = int(max(SEEDANCE_MIN_SEC, min(SEEDANCE_MAX_SEC, round(seconds))))
    image_uri = fal_client.upload_file(image_path)
    last_err = None
    for attempt in range(1, 4):
        try:
            result = fal_client.subscribe(
                SEEDANCE_MODEL,
                arguments={
                    "prompt": prompt,
                    "image_url": image_uri,
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "duration": dur,
                    "generate_audio": False,   # we lay the narrator voice over it
                },
                with_logs=False,
            )
            url = result["video"]["url"] if isinstance(result.get("video"), dict) \
                else result["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return float(dur)
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


def overlay_voice(video_path: str, audio_path: str, out_path: str):
    """Put the narrator voice onto the silent Seedance motion clip (ffmpeg, free).
    Keeps the video as-is and stops at whichever ends first, so the voiceover is
    fully heard over the motion."""
    subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,                  # silent Seedance motion
        "-i", audio_path,                  # narrator voice
        "-c:v", "copy",                    # don't re-encode the video
        "-c:a", "aac", "-ar", "44100",
        "-map", "0:v:0", "-map", "1:a:0",  # video from clip, audio from voice
        "-shortest",
        out_path,
    ], check=True, capture_output=True)


def narration_motion_prompt(ln: dict, setting: str) -> str:
    """Scene direction for the narration motion shot. Intro = people settling
    into the room; cliffhanger = a tense stand-up confrontation. Folds in the
    line's own camera cue if the writer gave one."""
    beat = (ln.get("beat") or "").strip().lower()
    camera = (ln.get("camera") or "").strip()
    # The scene image shows one lawyer SEATED at the desk and one STANDING, so the
    # motion fits that: the standing one gestures / leans in, or the seated one
    # rises to confront. Big enough to feel alive, small enough to stay in frame.
    if beat == "cliffhanger":
        action = ("the seated lawyer slowly stands up from the desk to confront "
                  "the other; a tense standoff")
    else:
        action = ("the standing lawyer leans over the desk and gestures while the "
                  "seated lawyer listens; charged, tense atmosphere")
    cam = camera or "slow cinematic push-in"
    return (f"{cam}. {action}. Set in {setting}, cinematic prestige legal drama, "
            f"photorealistic.")


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
    total_seconds = 0.0        # lip-sync-only talker seconds (veed/kling/omnihuman)
    image_cost = 0.0           # plain establishing image cost (fallback only)
    seedance_seconds = 0.0     # Seedance motion seconds (narration beats)
    dlg_seedance_seconds = 0.0  # Seedance motion seconds (combo dialogue clips)
    sync_seconds = 0.0         # Sync lip-sync seconds (combo dialogue clips)
    scene_cost = 0.0           # two-character scene image cost (once)
    print(f"Making clips for {len(lines)} lines ...")

    for i, ln in enumerate(lines, 1):
        name = ln["character"]
        audio_file = ln.get("audio")
        if not audio_file:
            print(f"  [{i}] WARNING: no audio for '{name}', skipping.")
            continue
        audio_path = os.path.join(OUT_DIR, audio_file)

        # --- NARRATION line: Seedance MOTION scene + voiceover ---
        # No talking face here, so instead of a frozen photo we animate a
        # two-character scene (people moving, standing up) and lay the narrator
        # voice over it. If Seedance fails, fall back to the old static shot so
        # the line is never lost.
        if ln.get("type") == "narration" or name == "Narrator":
            file_name = f"clip_{i:02d}_narrator.mp4"
            out_path = os.path.join(OUT_DIR, file_name)
            try:
                scene_path, added = ensure_scene_image(setting)
                scene_cost += added
                voice_len = audio_duration(audio_path)
                tmp_motion = os.path.join(OUT_DIR, f"_motion_{i:02d}.mp4")
                secs = make_seedance_clip(
                    scene_path, narration_motion_prompt(ln, setting),
                    voice_len, tmp_motion)
                seedance_seconds += secs
                overlay_voice(tmp_motion, audio_path, out_path)
                os.remove(tmp_motion)
                print(f"  [{i}] Narrator: saved {file_name} (Seedance motion {secs:.0f}s)")
            except Exception as e:
                print(f"  [{i}] Narrator: Seedance failed ({e}); using static shot")
                est_path, added = ensure_establishing(setting)
                image_cost += added
                make_narration_clip(est_path, audio_path, out_path)
            ln["clip"] = file_name
            made += 1
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

        # --- DIALOGUE line: lip-sync talking face (VEED / Kling / OmniHuman) ---
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
        if TALKING_MODEL == "seedance_sync":
            # THE COMBO (default): Seedance acts the body + Sync lip-syncs our
            # voice. Reuse the cached uploaded image URL across this character's
            # lines. The line's action/emotion/camera drive the motion prompt.
            if name not in uploaded_image:
                uploaded_image[name] = fal_client.upload_file(img_path)
            sd_secs, sy_secs = make_acting_clip(
                uploaded_image[name], audio_path,
                dialogue_acting_prompt(ln, setting), out_path)
            dlg_seedance_seconds += sd_secs
            sync_seconds += sy_secs
            log_secs = sy_secs
        elif TALKING_MODEL == "omnihuman":
            # OmniHuman: send image + audio inline (data URIs); the character
            # moves and acts from the scene prompt.
            log_secs = make_omnihuman_clip(
                img_path, audio_path, motion_prompt(ln, setting), out_path)
            total_seconds += log_secs
        elif TALKING_MODEL == "veed":
            # VEED Fabric: upload the image (once) + audio; lip-sync only, and
            # the clip length matches the voice (no padding).
            if name not in uploaded_image:
                uploaded_image[name] = fal_client.upload_file(img_path)
            audio_url = fal_client.upload_file(audio_path)
            log_secs = make_veed_clip(uploaded_image[name], audio_url, out_path)
            total_seconds += log_secs
        else:
            # Kling: upload the image (once) + audio, feed emotion as a small nudge.
            if name not in uploaded_image:
                uploaded_image[name] = fal_client.upload_file(img_path)
            audio_url = fal_client.upload_file(audio_path)
            emotion = (ln.get("emotion") or "").strip()
            kp = f"{emotion} facial expression, subtle natural movement" if emotion else ""
            log_secs = make_talking_clip(uploaded_image[name], audio_url, out_path, kp)
            total_seconds += log_secs
        ln["clip"] = file_name
        made += 1
        print(f"  [{i}] {name}: saved {file_name} ({log_secs:.1f}s)")

    # Cost = dialogue clips + narration motion + images. The dialogue cost depends
    # on which talking model ran (the combo bills Seedance + Sync; the others bill
    # a single per-second rate).
    if TALKING_MODEL == "omnihuman":
        talking_rate, model_label = costs.OMNIHUMAN_PER_SEC, "OmniHuman 1.5"
    elif TALKING_MODEL == "veed":
        talking_rate, model_label = costs.VEED_FABRIC_480P_PER_SEC, "VEED Fabric 1.0 480p"
    elif TALKING_MODEL == "seedance_sync":
        talking_rate, model_label = 0.0, "Seedance + Sync"   # billed below, not per-sec
    else:
        talking_rate, model_label = costs.KLING_AVATAR_PER_SEC, "Kling AI Avatar v2 std"

    # Both narration beats and combo dialogue clips use Seedance; bill all the
    # Seedance seconds at one rate, and the Sync seconds at its own rate.
    seedance_cost = (seedance_seconds + dlg_seedance_seconds) * costs.SEEDANCE_PRO_PER_SEC
    sync_cost = sync_seconds * costs.SYNC_LIPSYNC_PER_SEC
    clip_cost = (total_seconds * talking_rate + seedance_cost + sync_cost
                 + scene_cost + image_cost)

    # Build a clear label naming every model that ran for this step.
    if TALKING_MODEL == "seedance_sync":
        parts = [f"Seedance+Sync acting dialogue ({dlg_seedance_seconds:.0f}s motion "
                 f"+ {sync_seconds:.0f}s lip-sync)"]
    else:
        parts = [f"{model_label} talking ({total_seconds:.1f}s)"]
    if seedance_seconds:
        parts.append(f"Seedance narration motion ({seedance_seconds:.0f}s)")
    if scene_cost:
        parts.append("Nano Banana Pro scene image")
    if image_cost:
        parts.append("FLUX dev establishing shot")
    costs.record(data, "clips", "Clips - " + " + ".join(parts), clip_cost)

    # Save the clip file names back into analysis.json (one place for all).
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the clips.")
    if TALKING_MODEL == "seedance_sync":
        summary = (f"{made} clips (Seedance+Sync acting {dlg_seedance_seconds:.0f}s "
                   f"+ narration motion {seedance_seconds:.0f}s)")
    else:
        summary = (f"{made} clips ({model_label} {total_seconds:.1f}s talking "
                   f"+ Seedance {seedance_seconds:.0f}s motion)")
    costs.show(summary, clip_cost)


if __name__ == "__main__":
    main()
