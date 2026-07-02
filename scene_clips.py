"""STAGE 2 - STEP 3: Make one cinematic CLIP per SCENE (Kling scene pipeline).

NEW MODEL: instead of one talking avatar per line, we render one SHOT per scene
where the characters act and talk TO EACH OTHER in the same room — a real short
film. Two kinds of scene:

  DIALOGUE scene:
    1. Compose ONE image with the scene's 1-2 characters together in the setting
       (Nano Banana Pro edit, using their locked portraits as references so the
       faces stay consistent).
    2. Animate it with Kling v3 (image-to-video): the characters move, act, and
       SPEAK the dialogue in OUR cloned voices (voice_ids from voice_maker.py),
       with native lip-sync. Kling allows 2 voices per shot.

  NARRATION scene (hook / cliffhanger):
    Establishing shot of the setting with the main characters, animated with
    Seedance (silent), with the Narrator's ElevenLabs voice laid over it.

Usage:
    python scene_clips.py

Reads:  output/analysis.json   (characters w/ portraits + voice_ids, script scenes)
Output: output/clip_01.mp4 ...   (one per scene, in order); writes scene["clip"].
Cost:   Kling ~$0.126-0.154/s (dialogue) + Seedance $0.026/s (narration) +
        $0.15 per composed scene image.
"""
import os
import sys
import json
import re
import time
import subprocess
import requests
import fal_client
from PIL import Image
from dotenv import load_dotenv
import costs

load_dotenv()
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

OUT_DIR = "output"
KLING_MODEL = "fal-ai/kling-video/v3/standard/image-to-video"   # scene + dialogue
SCENE_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"      # compose chars into a scene
SEEDANCE_MODEL = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"  # narration
KLING_MIN_SEC, KLING_MAX_SEC = 5, 15


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def is_portrait(path: str) -> bool:
    w, h = Image.open(path).size
    return h > w


def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 5.0


def character_gender(c: dict) -> str:
    g = str(c.get("gender", "")).strip().lower()
    if g in ("male", "female"):
        return g
    text = f"{c.get('role','')} {c.get('personality','')}".lower()
    return "female" if any(w in text for w in ["woman", "female", "she ", "daughter"]) else "male"


def descriptor(c: dict) -> str:
    """A short visual handle Kling can attach a line to, e.g. 'the older barrister'."""
    role = (c.get("role") or "").strip().lower()
    if role:
        return f"the {role}"
    return "the woman" if character_gender(c) == "female" else "the man"


# --- Scene image: put the characters together in the setting ---------------

def compose_scene_image(portrait_paths: list, setting: str, shot: str,
                        action: str, out_path: str) -> bool:
    """Compose the given character portraits into ONE upright vertical scene image
    (Nano Banana Pro edit). Keeps their exact faces. Returns True on success.

    Composed VERTICALLY (people in front, room rising behind/above) so the model
    does not rotate a wide layout sideways — the same lesson as make_scene_image."""
    urls = [fal_client.upload_file(p) for p in portrait_paths]
    prompt = (
        f"Put these people together in ONE cinematic shot inside {setting}. "
        f"{action}. Keep their exact faces and clothing. {shot}. Vertical 9:16 "
        f"portrait, upright: the people stand/sit in the foreground with heads "
        f"near the TOP of the frame, the room rising behind and above them. Moody "
        f"cinematic prestige legal-drama lighting, photorealistic. NOT rotated, "
        f"NOT sideways, NOT landscape."
    )
    for attempt in range(1, 3):
        p = prompt if attempt == 1 else prompt + " CRITICAL: upright vertical frame."
        try:
            r = fal_client.subscribe(
                SCENE_EDIT_MODEL,
                arguments={"image_urls": urls, "prompt": p,
                           "aspect_ratio": "9:16", "resolution": "2K", "num_images": 1},
                with_logs=False,
            )
            url = r["images"][0]["url"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            if is_portrait(out_path):
                return True
            print(f"    scene image came out landscape; retrying ({attempt})")
        except Exception as e:
            print(f"    compose attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    return os.path.exists(out_path)


# --- Kling dialogue scene --------------------------------------------------

def scene_duration(scene: dict) -> str:
    """Pick a Kling clip length (5-15s) long enough to speak all the lines."""
    words = sum(len(d["line"].split()) for d in scene.get("dialogue", []))
    secs = round(words / 2.3 + 2)          # ~2.3 words/sec of speech + a little air
    return str(max(KLING_MIN_SEC, min(KLING_MAX_SEC, secs)))


def build_dialogue_prompt(scene: dict, chars_by_name: dict):
    """Turn a dialogue scene into a Kling prompt + the ordered voice_ids.
    Each character's line is tagged with <<<voice_1>>> / <<<voice_2>>> so Kling
    speaks it in that character's cloned voice."""
    voice_ids = []
    marker = {}
    for name in scene.get("characters", []):
        c = chars_by_name.get(name, {})
        vid = c.get("kling_voice_id")
        if vid:
            voice_ids.append(vid)
            marker[name] = f"<<<voice_{len(voice_ids)}>>>"   # 1-based, aligns with voice_ids
        else:
            marker[name] = ""   # no cloned voice -> Kling picks one
    desc = {n: descriptor(chars_by_name.get(n, {})) for n in scene.get("characters", [])}

    parts = [scene.get("shot", "medium two-shot, slow dolly in"),
             scene.get("action", "")]
    body = []
    for d in scene.get("dialogue", []):
        n = d["character"]
        tone = f", {d['emotion']}," if d.get("emotion") else ""
        m = marker.get(n, "")
        who = desc.get(n, "the person").capitalize()
        body.append(f'{who}{tone} says: {m} "{d["line"]}"')
    prompt = ". ".join(p for p in parts if p).strip(". ") + ". " + " ".join(body) + \
        " They face each other in the same room. Moody cinematic prestige legal " \
        "drama, photorealistic."
    return prompt, voice_ids


def make_kling_scene(image_path: str, prompt: str, voice_ids: list,
                     duration: str, out_path: str) -> float:
    """Animate the composed scene image into a talking dialogue clip with Kling v3.
    Returns the duration in seconds (for cost). Retries on transient fal errors."""
    image_url = fal_client.upload_file(image_path)
    args = {"image_url": image_url, "prompt": prompt,
            "duration": duration, "generate_audio": True}
    if voice_ids:
        args["voice_ids"] = voice_ids     # our cloned per-character voices
    last_err = None
    for attempt in range(1, 4):
        try:
            r = fal_client.subscribe(KLING_MODEL, arguments=args, with_logs=False)
            url = r["video"]["url"] if isinstance(r.get("video"), dict) else r["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return float(duration)
        except Exception as e:
            last_err = e
            print(f"    kling attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


# --- Seedance narration motion + narrator voice ----------------------------

def make_seedance_clip(image_path: str, prompt: str, seconds: float,
                       out_path: str) -> float:
    """Animate the establishing image (silent) for a narration beat. Made a bit
    LONGER than the voice so overlay_voice can trim it to the exact voice length."""
    dur = int(max(4, min(12, round(seconds + 1))))
    image_url = fal_client.upload_file(image_path)
    last_err = None
    for attempt in range(1, 4):
        try:
            r = fal_client.subscribe(
                SEEDANCE_MODEL,
                arguments={"prompt": prompt, "image_url": image_url,
                           "aspect_ratio": "9:16", "resolution": "720p",
                           "duration": dur, "generate_audio": False},
                with_logs=False,
            )
            url = r["video"]["url"] if isinstance(r.get("video"), dict) else r["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return float(dur)
        except Exception as e:
            last_err = e
            print(f"    seedance attempt {attempt} failed ({e}); retrying in 5s ...")
            time.sleep(5)
    raise last_err


def overlay_voice(video_path: str, audio_path: str, out_path: str):
    """Lay the narrator voice on the silent motion clip and cut the (longer) video
    to the voice's exact length (real frames, no padding, full last word)."""
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest", out_path,
    ], check=True, capture_output=True)


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")
    with open(analysis_path) as f:
        data = json.load(f)

    script = data.get("script")
    if not script or not script.get("scenes"):
        sys.exit("No scenes found. Run scene_writer.py first.")
    scenes = script["scenes"]
    chars_by_name = {c["fictional_name"]: c for c in data.get("characters", [])}
    setting = script.get("setting", "a law office")

    # The main characters (first two named) fill the narration establishing shots.
    named = [c for c in data.get("characters", []) if not c.get("anonymous")]
    main_portraits = [os.path.join(OUT_DIR, c["file"]) for c in named[:2]
                      if c.get("file")]

    kling_seconds = 0.0
    seedance_seconds = 0.0
    scene_images = 0
    print(f"Rendering {len(scenes)} scenes ...")

    for i, sc in enumerate(scenes, 1):
        clip_name = f"clip_{i:02d}.mp4"
        clip_path = os.path.join(OUT_DIR, clip_name)
        img_path = os.path.join(OUT_DIR, f"scene_{i:02d}.png")

        if sc.get("type") == "narration":
            # Establishing shot (main characters in the setting) + narrator VO.
            portraits = main_portraits or [
                os.path.join(OUT_DIR, c["file"]) for c in data["characters"][:1]
                if c.get("file")]
            if compose_scene_image(portraits, setting, sc.get("shot", "wide establishing shot"),
                                   sc.get("action", "the room, tense and quiet"), img_path):
                scene_images += 1
            audio_path = os.path.join(OUT_DIR, sc.get("audio", ""))
            voice_len = audio_duration(audio_path) if os.path.exists(audio_path) else 6.0
            tmp = os.path.join(OUT_DIR, f"_motion_{i:02d}.mp4")
            secs = make_seedance_clip(
                img_path, f"{sc.get('shot','')}. {sc.get('action','')}. "
                          f"Slow cinematic motion, {setting}.", voice_len, tmp)
            seedance_seconds += secs
            overlay_voice(tmp, audio_path, clip_path)
            os.remove(tmp)
            print(f"  [{i}] NARRATION -> {clip_name} (Seedance {secs:.0f}s + narrator VO)")
        else:
            # Dialogue scene: compose the speakers together, then Kling talks it.
            portraits = [os.path.join(OUT_DIR, chars_by_name[n]["file"])
                         for n in sc.get("characters", [])
                         if chars_by_name.get(n, {}).get("file")]
            if not portraits:
                print(f"  [{i}] DIALOGUE skipped: no character images.")
                continue
            if compose_scene_image(portraits, sc.get("setting", setting),
                                   sc.get("shot", "medium two-shot"),
                                   sc.get("action", ""), img_path):
                scene_images += 1
            prompt, voice_ids = build_dialogue_prompt(sc, chars_by_name)
            dur = scene_duration(sc)
            secs = make_kling_scene(img_path, prompt, voice_ids, dur, clip_path)
            kling_seconds += secs
            who = " + ".join(sc.get("characters", []))
            print(f"  [{i}] DIALOGUE [{who}] -> {clip_name} (Kling {secs:.0f}s, "
                  f"{len(voice_ids)} cloned voices)")

        sc["clip"] = clip_name

    # --- Cost: Kling dialogue + Seedance narration + composed scene images ---
    # Kling with cloned voices uses the "voice control" tier.
    kling_cost = kling_seconds * costs.KLING_V3_STD_VOICE_PER_SEC
    seedance_cost = seedance_seconds * costs.SEEDANCE_PRO_PER_SEC
    image_cost = scene_images * costs.NANO_BANANA_PRO_PER_IMAGE
    clip_cost = kling_cost + seedance_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Kling v3 dialogue ({kling_seconds:.0f}s) + Seedance "
                 f"narration ({seedance_seconds:.0f}s) + {scene_images} scene images",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated output/analysis.json with the scene clips.")
    costs.show(f"{len(scenes)} scenes (Kling {kling_seconds:.0f}s + "
               f"Seedance {seedance_seconds:.0f}s + {scene_images} images)", clip_cost)


if __name__ == "__main__":
    main()
