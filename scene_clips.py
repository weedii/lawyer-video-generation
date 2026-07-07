"""STAGE 2 - STEP 3: Make one cinematic CLIP per SCENE (Veo + our-voice pipeline).

The unit is a SCENE — a real short-film shot where the characters act and talk TO
EACH OTHER in the same room. Two kinds of scene:

  DIALOGUE scene:
    1. Compose ONE image with the scene's on-screen cast together in the setting
       (Nano Banana Pro edit, using their locked portraits so faces stay the same).
    2. Render ONE Veo 3.1 clip PER LINE: the speaker acts and says that line while
       the others stay in frame reacting (Veo animates + speaks it in ITS OWN
       voice, with native lip-sync). Because each clip has ONE speaker, we then
    3. RE-VOICE it into OUR locked ElevenLabs voice with Speech-to-Speech, which
       keeps the exact timing so the lip-sync still matches. Line clips are chained
       (last frame -> next clip's start image) and concatenated into one scene clip.

  NARRATION scene (hook / bridge / cliffhanger) — MEMOIR style:
    The lone PROTAGONIST performs the narration straight to camera (first person),
    one Veo clip, re-voiced into their own ElevenLabs voice.

Why this shape: Veo gives the best two-people-in-one-shot acting + lip-sync, but
its voice is invented and drifts. Speech-to-Speech swaps it for our consistent
per-character voice without breaking the lips (timing preserved). Result: two
actors in one room + our voices + consistency.

Usage:
    python scene_clips.py

Reads:  output/analysis.json   (characters w/ portraits + voice_id, script scenes)
Output: output/clip_01.mp4 ...   (one per scene, in order); writes scene["clip"].
Cost:   Veo 3.1 fast ~$0.15/s (audio) + ElevenLabs Speech-to-Speech ~$0.002/s +
        $0.15 per composed scene image.
"""
import os
import sys
import json
import re
import time
import shutil
import subprocess
import requests
import fal_client
from PIL import Image
from dotenv import load_dotenv
import costs

load_dotenv()
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVEN_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty (needed to re-voice into our voices).")

OUT_DIR = "output"
VEO_MODEL = "fal-ai/veo3.1/fast/image-to-video"   # two-person scene + native lip-sync
SCENE_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"  # compose chars into one shot
STS_MODEL = "eleven_english_sts_v2"               # ElevenLabs voice changer (keeps timing)
VEO_RES = "720p"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def is_portrait(path: str) -> bool:
    w, h = Image.open(path).size
    return h > w


def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def character_gender(c: dict) -> str:
    g = str(c.get("gender", "")).strip().lower()
    if g in ("male", "female"):
        return g
    text = f"{c.get('role','')} {c.get('personality','')}".lower()
    return "female" if any(w in text for w in ["woman", "female", "she ", "daughter"]) else "male"


def descriptor(c: dict) -> str:
    """A short visual handle for a character, e.g. 'the older barrister'."""
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
    does not rotate a wide layout sideways. room_ref keeps the SAME room when the
    cast changes (so a later shot in the same place isn't a different building)."""
    urls = [fal_client.upload_file(p) for p in portrait_paths]
    n = len(urls)
    only = (f"EXACTLY {n} " + ("person" if n == 1 else "people") +
            f" in the frame — only the {n} shown in the reference photos, and NO "
            f"other person: no third person, no bystander, no extra face, no crowd.")
    # Eyelines: 2 people must LOOK AT EACH OTHER (not all stare the same way, as if
    # a 4th person were off-camera). 1 person (narration) is handled by its prompt.
    face = ("" if n < 2 else
            f" The {n} people FACE EACH OTHER and look at one another, mid-conversation "
            f"— NOT all looking the same direction, NOT looking at the camera.")
    if room_ref and os.path.exists(room_ref):
        urls.append(fal_client.upload_file(room_ref))   # room reference goes LAST
        prompt = (
            f"The first {n} image(s) are people; the LAST image is a room. Place "
            f"those people together inside the SAME room shown in the last image — "
            f"keep that room's EXACT architecture, windows, wood panelling, "
            f"furniture, lighting and colour so it is unmistakably the identical "
            f"location. {only}{face} {action}. Keep each person's exact face and clothing. "
            f"{shot}. Vertical 9:16 portrait, upright: the people stand/sit in the "
            f"foreground with heads near the TOP of the frame, the room rising "
            f"behind and above them. Photorealistic. NOT rotated, NOT sideways, "
            f"NOT landscape."
        )
    else:
        prompt = (
            f"Put these people together in ONE cinematic shot inside {setting}. "
            f"{only}{face} {action}. Keep their exact faces and clothing. {shot}. Vertical 9:16 "
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


# --- Veo clip + re-voice ---------------------------------------------------

def veo_duration(text: str) -> str:
    """Pick a Veo length (4s/6s/8s — Veo only allows these) for one spoken line."""
    n = len((text or "").split())
    secs = n / 2.5 + 1.5           # ~2.5 words/sec + a little air
    return "4s" if secs <= 4 else "6s" if secs <= 6 else "8s"


def _short_err(e) -> str:
    """One short line from a fal error (never the whole echoed request/prompt)."""
    s = str(e)
    low = s.lower()
    if "content_policy" in low or "content checker" in low or "flagged" in low:
        return "blocked by Veo's content filter (wording too explicit)"
    return s[:140]


def make_veo_clip(image_path: str, prompt: str, duration: str, out_path: str) -> float:
    """Animate the start image into a talking clip with Veo 3.1 fast (its own voice
    + native lip-sync). Returns the billed seconds, or 0.0 (and writes no file) if
    Veo could not make it — so one bad line never crashes the whole run. Retries
    only transient errors; a content-filter block is permanent for that wording, so
    we stop retrying it (auto_fix already had its one shot)."""
    image_url = fal_client.upload_file(image_path)
    for attempt in range(1, 4):
        try:
            r = fal_client.subscribe(
                VEO_MODEL,
                arguments={"prompt": prompt, "image_url": image_url,
                           "duration": duration, "resolution": VEO_RES,
                           "generate_audio": True, "aspect_ratio": "9:16",
                           "auto_fix": True},   # let Veo soften a borderline prompt itself
                with_logs=False,
            )
            url = r["video"]["url"] if isinstance(r.get("video"), dict) else r["video"]
            with open(out_path, "wb") as f:
                f.write(requests.get(url).content)
            return float(int(duration[:-1]))
        except Exception as e:
            msg = _short_err(e)
            print(f"    veo attempt {attempt} failed: {msg}")
            if "content filter" in msg:          # same wording will fail again — give up
                return 0.0
            time.sleep(5)
    return 0.0


def revoice(clip_path: str, voice_id: str, out_path: str) -> float:
    """Swap the clip's (Veo-invented) voice for OUR ElevenLabs voice with
    Speech-to-Speech, which preserves the original timing so the lip-sync still
    matches. Muxes the new voice back onto the SAME video. Returns the audio
    seconds (for cost). On any failure, keeps Veo's original voice so a line is
    never lost."""
    if not voice_id:
        shutil.copyfile(clip_path, out_path)
        return 0.0
    aud = clip_path + ".aud.mp3"
    swap = clip_path + ".swap.mp3"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", clip_path, "-vn",
                        "-c:a", "libmp3lame", "-q:a", "2", aud],
                       check=True, capture_output=True)
        with open(aud, "rb") as f:
            r = requests.post(
                f"https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}",
                headers={"xi-api-key": ELEVEN_KEY},
                data={"model_id": STS_MODEL, "remove_background_noise": "true"},
                files={"audio": ("veo.mp3", f, "audio/mpeg")},
                timeout=180,
            )
        if r.status_code != 200:
            print(f"    revoice failed {r.status_code}: {r.text[:120]}; keeping Veo voice")
            shutil.copyfile(clip_path, out_path)
            return 0.0
        with open(swap, "wb") as f:
            f.write(r.content)
        # Put the new voice on the video; -shortest matches the (equal-length) pair.
        subprocess.run(["ffmpeg", "-y", "-i", clip_path, "-i", swap,
                        "-c:v", "copy", "-c:a", "aac",
                        "-map", "0:v:0", "-map", "1:a:0", "-shortest", out_path],
                       check=True, capture_output=True)
        return audio_duration(swap)
    except Exception as e:
        print(f"    revoice error ({e}); keeping Veo voice")
        shutil.copyfile(clip_path, out_path)
        return 0.0
    finally:
        for f in (aud, swap):
            if os.path.exists(f):
                os.remove(f)


def concat_clips(paths: list, out_path: str):
    """Join a scene's per-line clips into one scene clip (they share Veo's size +
    codec, so a stream copy works; re-encode as a fallback)."""
    if len(paths) == 1:
        shutil.copyfile(paths[0], out_path)
        return
    listf = out_path + ".txt"
    with open(listf, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                        "-c", "copy", out_path], capture_output=True)
    if r.returncode != 0 or not os.path.exists(out_path):
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        out_path], check=True, capture_output=True)
    os.remove(listf)


def extract_last_frame(clip_path: str, out_path: str) -> bool:
    """Save a still of a clip's ENDING (~0.2s before the end so it's clean), used
    to start the next line's clip so the action carries forward."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-sseof", "-0.2", "-i", clip_path,
            "-update", "1", "-frames:v", "1", "-q:v", "2", out_path,
        ], check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    (could not grab last frame for continuity: {e})")
        return False


# --- Prompts ---------------------------------------------------------------

def line_prompt(sc: dict, speaker_c: dict, listener_descs: list,
                line: str, emotion: str) -> str:
    """Veo prompt for ONE dialogue line: the speaker acts + says it; the others
    stay in frame reacting silently; nobody faces the camera."""
    who = descriptor(speaker_c).capitalize()
    tone = f", {emotion.strip()}," if emotion else ""
    # CRITICAL: exactly ONE voice in the clip. If the other person also talks,
    # the whole-clip voice-swap turns their line into the speaker's voice (a man
    # ends up speaking in the woman's voice). So force the listener SILENT.
    listen = ""
    if listener_descs:
        listen = (f" {' and '.join(listener_descs)} listens in silence, mouth "
                  f"closed, and does not speak — reacting only with their face.")
    return (
        "The scene is already in motion from the very first frame. "
        f"{sc.get('shot', 'medium two-shot, slow push-in')}. "
        f"Only {who} speaks{tone}: looking at the other person, they say just "
        f"these words: \"{line}\".{listen} They face one another and never look "
        "at or speak to the camera. Moody cinematic prestige legal drama, "
        "photorealistic."
    )


def narration_prompt(sc: dict, lead_c: dict) -> str:
    """Veo prompt for a memoir narration beat: the lone protagonist performs it
    straight to camera, first person."""
    who = descriptor(lead_c).capitalize() if lead_c else "The narrator"
    return (
        "The scene is already in motion from the very first frame. ONE person is "
        "ALONE in the shot. "
        f"{sc.get('shot', 'slow push-in on a lone figure')}. {sc.get('action', '')}. "
        f"{who} looks directly INTO the camera and tells us their own story, first "
        f"person, like a memoir confession: \"{sc.get('narration', '')}\". Nobody "
        "else is present. Moody cinematic prestige legal drama, photorealistic."
    )


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

    named = [c for c in data.get("characters", []) if not c.get("anonymous")]
    main_names = [c["fictional_name"] for c in named[:2] if c.get("file")]

    veo_seconds = 0.0       # Veo video seconds (billed $0.15/s w/ audio)
    sts_seconds = 0.0       # ElevenLabs Speech-to-Speech seconds
    scene_images = 0        # composed images we paid for
    location_ref = {}       # setting -> first image made there (keep the room identical)
    compose_cache = {}      # (same people, same place) -> reuse that image (no re-pay)
    print(f"Rendering {len(scenes)} scenes ...")

    for i, sc in enumerate(scenes, 1):
        clip_name = f"clip_{i:02d}.mp4"
        clip_path = os.path.join(OUT_DIR, clip_name)
        sc_setting = sc.get("setting", setting) or setting

        # Who is IN this shot.
        if sc.get("type") == "narration":
            names = [n for n in sc.get("characters", [])
                     if chars_by_name.get(n, {}).get("file")]
            if not names and main_names:
                names = [main_names[0]]
            if not names:
                print(f"  [{i}] NARRATION skipped: no lead portrait.")
                continue
            names = names[:1]
        else:
            # ONLY the 2 speakers go in the frame. Silent third wheels made the
            # cast all stare the same way (as if at a 4th person off-camera) and
            # doubled the cost by re-composing the same group every scene. A third
            # person who matters gets their OWN scene.
            names = [n for n in sc.get("characters", [])
                     if chars_by_name.get(n, {}).get("file")]
            if not names:
                print(f"  [{i}] DIALOGUE skipped: no character images.")
                continue
        portraits = [os.path.join(OUT_DIR, chars_by_name[n]["file"]) for n in names]

        loc_key = sc_setting.strip().lower()

        # Compose a CLEAN two-shot (both people neutral, mouths closed). We do NOT
        # reuse a previous clip's last frame — that frame is mid-talk, which makes
        # Veo continue the wrong speaker and breaks the one-speaker-per-clip rule the
        # voice-swap relies on. But if the SAME people were already composed in the
        # SAME place, reuse that image instead of paying to re-make a near-identical
        # one. And even for a new pair, location_ref keeps the room identical.
        cache_key = (frozenset(names), loc_key)
        start_img = compose_cache.get(cache_key)
        if start_img and os.path.exists(start_img):
            print(f"    (reusing scene image — same people, same place, $0 saved)")
        else:
            start_img = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
            room_ref = location_ref.get(loc_key)
            if compose_scene_image(portraits, sc_setting, sc.get("shot", ""),
                                   sc.get("action", ""), start_img, room_ref=room_ref):
                scene_images += 1
                location_ref.setdefault(loc_key, start_img)
                compose_cache[cache_key] = start_img

        # --- NARRATION: one Veo clip, protagonist to camera, then re-voice ---
        if sc.get("type") == "narration":
            lead_c = chars_by_name.get(names[0], {})
            raw = os.path.join(OUT_DIR, f"_raw_{i:02d}.mp4")
            secs = make_veo_clip(start_img, narration_prompt(sc, lead_c),
                                 veo_duration(sc.get("narration", "")), raw)
            if secs == 0.0 or not os.path.exists(raw):
                print(f"  [{i}] NARRATION skipped: Veo could not generate this beat.")
                continue
            veo_seconds += secs
            sts_seconds += revoice(raw, lead_c.get("voice_id", ""), clip_path)
            if os.path.exists(raw):
                os.remove(raw)
            sc["clip"] = clip_name
            print(f"  [{i}] NARRATION [{names[0]}] -> {clip_name} (Veo {secs:.0f}s, re-voiced)")
            continue

        # --- DIALOGUE: one Veo clip PER LINE, chained, then concatenated ---
        speakers = sc.get("characters", [])
        line_clips = []
        # Every line starts from the SAME clean two-shot (both neutral, mouths
        # closed) — NOT the previous clip's mid-talk last frame. Starting mid-talk
        # made Veo keep the previous speaker going (their audio then got swapped
        # into the next speaker's voice) and garble the new line. A clean start per
        # line keeps each clip to ONE speaker so the voice-swap is correct.
        for j, d in enumerate(sc.get("dialogue", []), 1):
            spk = d.get("character")
            spk_c = chars_by_name.get(spk, {})
            listener_descs = [descriptor(chars_by_name[n]) for n in names if n != spk]
            raw = os.path.join(OUT_DIR, f"_raw_{i:02d}_{j:02d}.mp4")
            line_out = os.path.join(OUT_DIR, f"_line_{i:02d}_{j:02d}.mp4")
            secs = make_veo_clip(
                start_img, line_prompt(sc, spk_c, listener_descs, d["line"], d.get("emotion", "")),
                veo_duration(d["line"]), raw)
            if secs == 0.0 or not os.path.exists(raw):
                print(f"    line {j} skipped (Veo could not generate it).")
                continue
            veo_seconds += secs
            sts_seconds += revoice(raw, spk_c.get("voice_id", ""), line_out)
            if os.path.exists(raw):
                os.remove(raw)
            line_clips.append(line_out)

        if not line_clips:
            print(f"  [{i}] DIALOGUE skipped: no lines.")
            continue
        concat_clips(line_clips, clip_path)
        for lc in line_clips:                       # tidy the per-line temp clips
            if os.path.exists(lc):
                os.remove(lc)
        sc["clip"] = clip_name
        print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {clip_name} "
              f"({len(line_clips)} lines, Veo + re-voiced)")

    # --- Cost: Veo clips + Speech-to-Speech re-voicing + composed images ---
    veo_cost = veo_seconds * costs.VEO_FAST_AUDIO_PER_SEC
    sts_cost = sts_seconds * costs.ELEVEN_STS_PER_SEC
    image_cost = scene_images * costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
    clip_cost = veo_cost + sts_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Veo 3.1 fast ({veo_seconds:.0f}s) + ElevenLabs voice swap "
                 f"({sts_seconds:.0f}s) + {scene_images} scene images",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated output/analysis.json with the scene clips. "
          f"({scene_images} scene images composed)")
    costs.show(f"{len(scenes)} scenes (Veo {veo_seconds:.0f}s + voice swap "
               f"{sts_seconds:.0f}s + {scene_images} images)", clip_cost)


if __name__ == "__main__":
    main()
