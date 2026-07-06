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

# Negative prompt for the Kling dialogue clips: things we do NOT want. The big
# one is the dead opening — Kling likes to hold the first frame with the
# characters staring at the camera before they act. Naming it here (plus the
# "start in motion" line in the positive prompt) cuts that lead-in down;
# assemble.py trims whatever remains.
KLING_NEG_PROMPT = (
    "looking at camera, staring at the camera, talking to camera, addressing "
    "the viewer, facing the camera, eye contact with camera, "
    "static opening, frozen first frame, motionless pause at the start, "
    "standing still doing nothing, waiting before speaking, idle, delayed "
    "speech, slow to start, "
    # Kling also likes to invent a garbled filler sound at the very start (the
    # wrong character mumbling something meaningless before the real line) — an
    # audio version of the dead lead-in. Name it so it happens less.
    "mumbling, muttering, garbled speech, gibberish, nonsense words, "
    "unintelligible talking, the wrong character speaking first, "
    "background chatter, lip movement with no clear words, "
    "blur, distort, low quality"   # keep the model's default quality guard too
)


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
                        action: str, out_path: str, room_ref: str = None) -> bool:
    """Compose the given character portraits into ONE upright vertical scene image
    (Nano Banana Pro edit). Keeps their exact faces. Returns True on success.

    Composed VERTICALLY (people in front, room rising behind/above) so the model
    does not rotate a wide layout sideways — the same lesson as make_scene_image.

    Used for the FIRST shot of a set of people (and for cast/location changes).
    Clip-to-clip continuity is NOT done here — Nano recomposes a new pose and
    loses it; the main loop instead feeds the previous clip's last frame straight
    to Kling for continuation clips.

    room_ref: an existing scene image of the SAME location (used when the cast
    changed). Keeps the room identical while placing the new people in it, so a
    later shot in the same place doesn't look like a different building."""
    urls = [fal_client.upload_file(p) for p in portrait_paths]
    n = len(urls)
    if room_ref and os.path.exists(room_ref):
        urls.append(fal_client.upload_file(room_ref))   # room reference goes LAST
        prompt = (
            f"The first {n} image(s) are people; the LAST image is a room. Place "
            f"those people together inside the SAME room shown in the last image — "
            f"keep that room's EXACT architecture, windows, wood panelling, "
            f"furniture, lighting and colour so it is unmistakably the identical "
            f"location. {action}. Keep each person's exact face and clothing. "
            f"{shot}. Vertical 9:16 portrait, upright: the people stand/sit in the "
            f"foreground with heads near the TOP of the frame, the room rising "
            f"behind and above them. Photorealistic. NOT rotated, NOT sideways, "
            f"NOT landscape."
        )
    else:
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

    # Lead with a "start in motion" directive. Kling (like all image-to-video)
    # tends to ease in from the still frame — the characters hold the opening
    # pose, looking at the camera, for a beat before they move/talk. Putting this
    # first (Kling weights early words most) pushes the action onto frame 1. It
    # only REDUCES the stare; assemble.py still trims whatever's left.
    parts = ["The scene is already in motion from the very first frame: the "
             "characters are mid-conversation, moving and speaking immediately. "
             "They look at and speak to EACH OTHER, facing one another like two "
             "people in a private conversation — they NEVER look at or talk to "
             "the camera, they are not addressing the viewer",
             scene.get("shot", "medium two-shot, slow dolly in"),
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
            "negative_prompt": KLING_NEG_PROMPT,
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


def extract_last_frame(clip_path: str, out_path: str) -> bool:
    """Save a still of a clip's ENDING as a PNG, for scene continuity.

    We grab ~0.2s before the very end (not the literal last frame, which can be
    motion-blurred or half-decoded) so we get a clean picture of where the
    characters ended up. The next clip of the same scene composes from this so
    the action continues instead of restarting. Free (local ffmpeg)."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-sseof", "-0.2", "-i", clip_path,
            "-update", "1", "-frames:v", "1", "-q:v", "2", out_path,
        ], check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    (could not grab last frame for continuity: {e})")
        return False


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
    main_names = [c["fictional_name"] for c in named[:2] if c.get("file")]

    kling_voice_seconds = 0.0  # Kling dialogue billed with voice control ($0.154/s)
    kling_plain_seconds = 0.0  # Kling dialogue with no cloned voice ($0.126/s)
    seedance_seconds = 0.0
    scene_images = 0            # composed images we actually paid for
    # SCENE CONTINUITY. The FIRST time a set of people appears in a place we
    # compose a portrait-anchored scene image. For the NEXT clip of the same
    # people in the same place, we start Kling STRAIGHT from the previous clip's
    # last frame (no recompose) so the action carries forward — someone who stood
    # up stays standing — instead of resetting and replaying. The faces in that
    # frame are already correct, so continuity costs nothing and doesn't drift.
    continuity_frame = {}      # (sorted names, setting) -> previous clip's last-frame PNG
    # When the CAST changes but the LOCATION is the same, there's no continuity to
    # carry, so we anchor to the first image shot in that location instead, so the
    # room stays identical rather than the model inventing a different courtroom.
    location_ref = {}          # setting (location only) -> first image made there
    print(f"Rendering {len(scenes)} scenes ...")

    for i, sc in enumerate(scenes, 1):
        clip_name = f"clip_{i:02d}.mp4"
        clip_path = os.path.join(OUT_DIR, clip_name)
        sc_setting = sc.get("setting", setting) or setting

        # Who is IN this shot (narration = the main characters; dialogue = the
        # scene's speakers), and their locked portraits.
        if sc.get("type") == "narration":
            names = main_names or [data["characters"][0]["fictional_name"]]
        else:
            names = [n for n in sc.get("characters", [])
                     if chars_by_name.get(n, {}).get("file")]
            if not names:
                print(f"  [{i}] DIALOGUE skipped: no character images.")
                continue
        portraits = [os.path.join(OUT_DIR, chars_by_name[n]["file"])
                     for n in names if chars_by_name.get(n, {}).get("file")]

        # Choose this clip's START IMAGE.
        loc_key = sc_setting.strip().lower()
        key = (tuple(sorted(names)), loc_key)
        cont_ref = continuity_frame.get(key) if sc.get("type") != "narration" else None

        if cont_ref:
            # CONTINUE the shot: start this clip STRAIGHT from the previous clip's
            # last frame (no Nano recompose). Nano is a composer — given the
            # portraits it rebuilds a brand-new pose/framing and throws the
            # continuity away (we saw it do exactly that). Feeding the real last
            # frame straight to Kling is the only thing that actually continues
            # the action. The faces in that frame are already correct (the first
            # clip of this pair was portrait-anchored), so continuity is free.
            img_path = cont_ref
            print(f"  [{i}] [{' + '.join(names)}] continues straight from the "
                  f"previous clip's last frame")
        else:
            # First time these people appear here (or the cast/location changed):
            # compose a fresh portrait-anchored scene image. If we've already shot
            # this room, anchor to it so it stays the identical location.
            img_path = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
            room_ref = location_ref.get(loc_key)
            if room_ref:
                print(f"  [{i}] composing [{' + '.join(names)}] in {sc_setting} "
                      f"(anchored to the established room)")
            if compose_scene_image(portraits, sc_setting, sc.get("shot", ""),
                                   sc.get("action", ""), img_path, room_ref=room_ref):
                scene_images += 1
                location_ref.setdefault(loc_key, img_path)   # first shot here = the room anchor

        if sc.get("type") == "narration":
            # Animate the establishing image (silent) + lay the narrator VO over it.
            audio_path = os.path.join(OUT_DIR, sc.get("audio", ""))
            voice_len = audio_duration(audio_path) if os.path.exists(audio_path) else 6.0
            tmp = os.path.join(OUT_DIR, f"_motion_{i:02d}.mp4")
            secs = make_seedance_clip(
                img_path, f"{sc.get('shot','')}. {sc.get('action','')}. "
                          f"Slow cinematic motion, {sc_setting}.", voice_len, tmp)
            seedance_seconds += secs
            overlay_voice(tmp, audio_path, clip_path)
            os.remove(tmp)
            print(f"  [{i}] NARRATION -> {clip_name} (Seedance {secs:.0f}s + narrator VO)")
        else:
            # Kling animates the scene image into a talking dialogue shot.
            prompt, voice_ids = build_dialogue_prompt(sc, chars_by_name)
            dur = scene_duration(sc)
            secs = make_kling_scene(img_path, prompt, voice_ids, dur, clip_path)
            # Bill the right Kling tier: voice control if we passed cloned voices.
            if voice_ids:
                kling_voice_seconds += secs
            else:
                kling_plain_seconds += secs
            # Remember where this clip ENDED, so the next clip of the same scene
            # (same people, same place) continues from here instead of resetting.
            end_frame = os.path.join(OUT_DIR, f"_end_{i:02d}.png")
            if extract_last_frame(clip_path, end_frame):
                continuity_frame[key] = end_frame
            print(f"  [{i}] DIALOGUE [{' + '.join(names)}] -> {clip_name} "
                  f"(Kling {secs:.0f}s, {len(voice_ids)} cloned voices)")

        sc["clip"] = clip_name

    # --- Cost: Kling dialogue + Seedance narration + composed scene images ---
    # Kling billed per tier: voice-control seconds ($0.154) vs plain-audio ($0.126).
    kling_seconds = kling_voice_seconds + kling_plain_seconds
    kling_cost = (kling_voice_seconds * costs.KLING_V3_STD_VOICE_PER_SEC
                  + kling_plain_seconds * costs.KLING_V3_STD_AUDIO_PER_SEC)
    seedance_cost = seedance_seconds * costs.SEEDANCE_PRO_PER_SEC
    image_cost = scene_images * costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
    clip_cost = kling_cost + seedance_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Kling v3 dialogue ({kling_seconds:.0f}s) + Seedance "
                 f"narration ({seedance_seconds:.0f}s) + {scene_images} scene images",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated output/analysis.json with the scene clips. "
          f"({scene_images} scene images composed, continuity-chained)")
    costs.show(f"{len(scenes)} scenes (Kling {kling_seconds:.0f}s + "
               f"Seedance {seedance_seconds:.0f}s + {scene_images} images)", clip_cost)


if __name__ == "__main__":
    main()
