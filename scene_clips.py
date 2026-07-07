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

  NARRATION scene (hook / bridge / cliffhanger) — MEMOIR style:
    The lone PROTAGONIST performs the narration straight to camera in their own
    cloned voice — first person, mouth moving, doing something (pacing a cell,
    walking a corridor) in a fitting place. Same Kling talking shot as dialogue
    but with ONE voice, and allowed to face the lens (dialogue never is).

Usage:
    python scene_clips.py

Reads:  output/analysis.json   (characters w/ portraits + voice_ids, script scenes)
Output: output/clip_01.mp4 ...   (one per scene, in order); writes scene["clip"].
Cost:   Kling ~$0.126-0.154/s (dialogue AND narration) + $0.15 per composed
        scene image.
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
KLING_MIN_SEC, KLING_MAX_SEC = 5, 15

# Negative prompts for the Kling clips: things we do NOT want.
#
# COMMON to every clip: the dead opening — Kling likes to hold the first frame,
# motionless, before anyone acts — plus the garbled filler sound it invents at
# the very start (an audio version of the dead lead-in). Naming these (with the
# "start in motion" line in the positive prompt) cuts the lead-in down;
# assemble.py trims whatever remains.
KLING_NEG_COMMON = (
    "static opening, frozen first frame, motionless pause at the start, "
    "standing still doing nothing, waiting before speaking, idle, delayed "
    "speech, slow to start, "
    "mumbling, muttering, garbled speech, gibberish, nonsense words, "
    "unintelligible talking, background chatter, lip movement with no clear "
    "words, blur, distort, low quality"   # keep the model's default quality guard
)
# DIALOGUE clips also ban facing the camera: the actors must talk to EACH OTHER,
# never to the viewer. (Narration is the ONE exception — the lone protagonist
# performs straight to camera — so it does NOT use these.)
KLING_NEG_CAMERA = (
    "looking at camera, staring at the camera, talking to camera, addressing "
    "the viewer, facing the camera, eye contact with camera, "
    "the wrong character speaking first"
)
KLING_NEG_DIALOGUE = KLING_NEG_CAMERA + ", " + KLING_NEG_COMMON
KLING_NEG_NARRATION = KLING_NEG_COMMON


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def is_portrait(path: str) -> bool:
    w, h = Image.open(path).size
    return h > w


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
    # HARD constraint: only the people whose photos we pass may appear. Otherwise,
    # if the action text names anyone else, Nano invents a random extra face
    # (wrong person, wrong gender) and consistency breaks.
    only = (f"EXACTLY {n} " + ("person" if n == 1 else "people") +
            f" in the frame — only the {n} shown in the reference photos, and NO "
            f"other person: no third person, no bystander, no extra face, no crowd.")
    if room_ref and os.path.exists(room_ref):
        urls.append(fal_client.upload_file(room_ref))   # room reference goes LAST
        prompt = (
            f"The first {n} image(s) are people; the LAST image is a room. Place "
            f"those people together inside the SAME room shown in the last image — "
            f"keep that room's EXACT architecture, windows, wood panelling, "
            f"furniture, lighting and colour so it is unmistakably the identical "
            f"location. {only} {action}. Keep each person's exact face and clothing. "
            f"{shot}. Vertical 9:16 portrait, upright: the people stand/sit in the "
            f"foreground with heads near the TOP of the frame, the room rising "
            f"behind and above them. Photorealistic. NOT rotated, NOT sideways, "
            f"NOT landscape."
        )
    else:
        prompt = (
            f"Put these people together in ONE cinematic shot inside {setting}. "
            f"{only} {action}. Keep their exact faces and clothing. {shot}. Vertical 9:16 "
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
    """Pick a Kling clip length (5-15s) long enough to speak all the lines
    (dialogue) or the whole narration (memoir beats)."""
    words = sum(len(d["line"].split()) for d in scene.get("dialogue", []))
    words += len(scene.get("narration", "").split())
    secs = round(words / 2.3 + 2)          # ~2.3 words/sec of speech + a little air
    return str(max(KLING_MIN_SEC, min(KLING_MAX_SEC, secs)))


def build_dialogue_prompt(scene: dict, chars_by_name: dict):
    """Turn a dialogue scene into a Kling prompt + the ordered voice_ids.
    Each character's line is tagged with <<<voice_1>>> / <<<voice_2>>> so Kling
    speaks it in that character's cloned voice."""
    speakers = scene.get("characters", [])
    onscreen = scene.get("onscreen") or speakers
    voice_ids = []
    marker = {}
    for name in speakers:                       # only the <=2 speakers get voices
        c = chars_by_name.get(name, {})
        vid = c.get("kling_voice_id")
        if vid:
            voice_ids.append(vid)
            marker[name] = f"<<<voice_{len(voice_ids)}>>>"   # 1-based, aligns with voice_ids
        else:
            marker[name] = ""   # no cloned voice -> Kling picks one
    desc = {n: descriptor(chars_by_name.get(n, {})) for n in onscreen}

    # Lead with a "start in motion" directive. Kling (like all image-to-video)
    # tends to ease in from the still frame — the characters hold the opening
    # pose, looking at the camera, for a beat before they move/talk. Putting this
    # first (Kling weights early words most) pushes the action onto frame 1. It
    # only REDUCES the stare; assemble.py still trims whatever's left.
    parts = ["The scene is already in motion from the very first frame: the "
             "characters are mid-conversation, moving and speaking immediately. "
             "They look at and speak to EACH OTHER, facing one another like people "
             "in a private conversation — they NEVER look at or talk to the camera, "
             "they are not addressing the viewer",
             scene.get("shot", "medium two-shot, slow dolly in"),
             scene.get("action", "")]
    body = []
    for d in scene.get("dialogue", []):
        n = d["character"]
        tone = f", {d['emotion']}," if d.get("emotion") else ""
        m = marker.get(n, "")
        who = desc.get(n, "the person").capitalize()
        body.append(f'{who}{tone} says: {m} "{d["line"]}"')
    # Name anyone present but NOT speaking, so Kling keeps them in frame reacting
    # (silent) instead of dropping them or making them talk.
    silent = [desc[n] for n in onscreen if n not in speakers]
    silent_note = ""
    if silent:
        who = " and ".join(s for s in silent)
        silent_note = (f" Also in the shot: {who} — present and reacting in silence, "
                       f"NOT speaking, no lip movement, but clearly visible.")
    prompt = ". ".join(p for p in parts if p).strip(". ") + ". " + " ".join(body) + \
        silent_note + " They are together in the same room. Moody cinematic " \
        "prestige legal drama, photorealistic."
    return prompt, voice_ids


def build_narration_prompt(scene: dict, chars_by_name: dict):
    """A memoir NARRATION beat: the lone protagonist performs straight TO CAMERA —
    first person, telling us the story while doing something (pacing a cell,
    walking a corridor), mouth moving, in their own cloned voice. This is the ONE
    place a character looks at the lens; dialogue scenes never do.
    Returns (prompt, voice_ids) — a single voice, the protagonist's."""
    names = scene.get("characters", [])
    name = names[0] if names else None
    c = chars_by_name.get(name, {})
    voice_ids = []
    marker = ""
    vid = c.get("kling_voice_id")
    if vid:
        voice_ids.append(vid)
        marker = "<<<voice_1>>>"
    who = descriptor(c).capitalize() if c else "The narrator"

    parts = ["The scene is already in motion from the very first frame. ONE person "
             "is ALONE in the shot, telling their own story straight to us: they "
             "look directly INTO the camera lens and address the viewer, like a "
             "first-person confession or memoir. Nobody else is present",
             scene.get("shot", "slow push-in on a lone figure"),
             scene.get("action", "")]
    line = scene.get("narration", "")
    body = f'{who} looks into the camera and says: {marker} "{line}"'
    prompt = ". ".join(p for p in parts if p).strip(". ") + ". " + body + \
        " Moody cinematic prestige legal drama, photorealistic."
    return prompt, voice_ids


def make_kling_scene(image_path: str, prompt: str, voice_ids: list,
                     duration: str, out_path: str,
                     negative_prompt: str = KLING_NEG_DIALOGUE) -> float:
    """Animate the composed scene image into a talking clip with Kling v3.
    Returns the duration in seconds (for cost). Retries on transient fal errors.
    negative_prompt defaults to the dialogue one (no facing camera); narration
    passes KLING_NEG_NARRATION so the lone narrator CAN look at us."""
    image_url = fal_client.upload_file(image_path)
    args = {"image_url": image_url, "prompt": prompt,
            "negative_prompt": negative_prompt,
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


# --- Scene continuity -------------------------------------------------------

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

    # The lead (first named) is the memoir narrator; main_names[0] is the fallback
    # if a narration scene somehow has no valid character on it.
    named = [c for c in data.get("characters", []) if not c.get("anonymous")]
    main_names = [c["fictional_name"] for c in named[:2] if c.get("file")]

    kling_voice_seconds = 0.0  # Kling billed with voice control ($0.154/s)
    kling_plain_seconds = 0.0  # Kling with no cloned voice ($0.126/s)
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

        # Who is IN this shot. NARRATION = the lone protagonist performing to
        # camera (a solo shot — one person only). DIALOGUE = the scene's speakers.
        if sc.get("type") == "narration":
            names = [n for n in sc.get("characters", [])
                     if chars_by_name.get(n, {}).get("file")]
            if not names and main_names:
                names = [main_names[0]]          # fallback: the lead
            if not names:
                print(f"  [{i}] NARRATION skipped: no lead portrait.")
                continue
            names = names[:1]                    # narration is solo
        else:
            # Compose from the FULL on-screen cast (speakers + silent reactors), so
            # everyone present stays in frame — only the <=2 speakers get voices.
            onscreen = sc.get("onscreen") or sc.get("characters", [])
            names = [n for n in onscreen if chars_by_name.get(n, {}).get("file")]
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
            # The lone protagonist PERFORMS the narration straight to camera, in
            # their own cloned voice (memoir style) — a Kling talking shot, one
            # voice, allowed to face the lens (KLING_NEG_NARRATION).
            prompt, voice_ids = build_narration_prompt(sc, chars_by_name)
            dur = scene_duration(sc)
            secs = make_kling_scene(img_path, prompt, voice_ids, dur, clip_path,
                                    negative_prompt=KLING_NEG_NARRATION)
            if voice_ids:
                kling_voice_seconds += secs
            else:
                kling_plain_seconds += secs
            print(f"  [{i}] NARRATION [{names[0]}] -> {clip_name} "
                  f"(Kling {secs:.0f}s, to camera, {len(voice_ids)} cloned voice)")
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

    # --- Cost: Kling clips (dialogue + narration) + composed scene images ---
    # Kling billed per tier: voice-control seconds ($0.154) vs plain-audio ($0.126).
    kling_seconds = kling_voice_seconds + kling_plain_seconds
    kling_cost = (kling_voice_seconds * costs.KLING_V3_STD_VOICE_PER_SEC
                  + kling_plain_seconds * costs.KLING_V3_STD_AUDIO_PER_SEC)
    image_cost = scene_images * costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
    clip_cost = kling_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Kling v3 ({kling_seconds:.0f}s, dialogue + narration) "
                 f"+ {scene_images} scene images",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated output/analysis.json with the scene clips. "
          f"({scene_images} scene images composed, continuity-chained)")
    costs.show(f"{len(scenes)} scenes (Kling {kling_seconds:.0f}s + "
               f"{scene_images} images)", clip_cost)


if __name__ == "__main__":
    main()
