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
       keeps the exact timing so the lip-sync still matches.
    Each line stays its OWN clip (a "beat") — we do NOT join them here. The editor
    (assemble.py) joins the beats with a continuous ambient bed and soft/staggered
    cuts, which is what makes the conversation read as one smooth scene.

  NARRATION scene (hook / bridge / cliffhanger) — MEMOIR style:
    The lone PROTAGONIST performs the narration straight to camera (first person),
    one Veo clip (one beat), re-voiced into their own ElevenLabs voice.

We also generate ONE looping ambient bed per location (ElevenLabs Sound-Effects)
and record it on each scene, so the editor can lay continuous room tone under the
cuts.

Why this shape: Veo gives the best two-people-in-one-shot acting + lip-sync, but
its voice is invented and drifts. Speech-to-Speech swaps it for our consistent
per-character voice without breaking the lips (timing preserved). Result: two
actors in one room + our voices + consistency.

Usage:
    python scene_clips.py

Reads:  output/analysis.json   (characters w/ portraits + voice_id, script scenes)
Output: output/beat_XX_YY.mp4 (dialogue) + clip_XX.mp4 (narration); writes each
        scene's ordered scene["beats"] list + scene["ambient"] bed.
Cost:   Veo 3.1 fast ~$0.15/s (audio) + ElevenLabs Speech-to-Speech ~$0.002/s +
        $0.15 per composed scene image + ~$0.002/s ambient beds.
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

# --- Coverage + reactions (make dialogue cut like a real film) --------------
# COVERAGE: instead of one static two-shot per scene, we compose a WIDE
# establishing two-shot + a close-up SINGLE of each speaker, and render each LINE
# from the speaker's single. Consecutive lines then cut A-single <-> B-single =
# real shot/reverse-shot instead of a frozen two-shot with a jump every line.
# REACTIONS: between lines we cut to a short SILENT clip of the LISTENER reacting,
# so the conversation breathes (research: the best short dramas are ~half silent
# reactions) and the cut to the next speaker is hidden behind the reaction.
COVERAGE = True           # compose singles + establishing wide (shot/reverse-shot)
REACTIONS = True          # insert silent listener reaction cutaways between lines
ESTABLISH_SEC = 2.5       # length of the FREE still-zoom establishing beat ($0, no Veo)
REACTION_DUR = "4s"       # Veo's shortest block; the editor caps silent beats short


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


def make_veo_clip(image_path: str, prompt: str, duration: str, out_path: str,
                  generate_audio: bool = True) -> float:
    """Animate the start image into a clip with Veo 3.1 fast. For dialogue lines we
    keep generate_audio=True (Veo's own voice + native lip-sync, which we later
    re-voice). For SILENT beats (reaction cutaways) we pass generate_audio=False so
    Veo doesn't invent a voice we'd have to strip. Returns the billed seconds, or
    0.0 (and writes no file) if Veo could not make it — so one bad beat never
    crashes the run. Retries transient errors; a content-filter block is permanent
    for that wording, so we stop retrying it (auto_fix already had its one shot)."""
    image_url = fal_client.upload_file(image_path)
    for attempt in range(1, 4):
        try:
            r = fal_client.subscribe(
                VEO_MODEL,
                arguments={"prompt": prompt, "image_url": image_url,
                           "duration": duration, "resolution": VEO_RES,
                           "generate_audio": generate_audio, "aspect_ratio": "9:16",
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


# --- Ambient bed: continuous room tone per location ------------------------
# The EDITOR (assemble.py) lays a seamlessly-looping ambient bed UNDER the whole
# scene so the audio never drops to silence at a cut — the single biggest trick
# for hiding the seams between separately-generated clips. We generate ONE short
# loop per unique location here (ElevenLabs Sound-Effects) and record it on each
# scene; the editor loops it to length and mixes it low under the dialogue.

def ambient_prompt(setting: str) -> str:
    """A short prompt describing the CONTINUOUS background sound of a place (room
    tone). Explicitly no music and no voices — just the steady bed of the room."""
    s = (setting or "a quiet room").strip().rstrip(".")
    return (f"Continuous quiet background room tone of {s}: subtle steady ambience, "
            f"faint distant sounds, no music, no speech, no voices — a seamless loop.")


def make_ambient(setting: str, out_path: str, seconds: int = 15) -> float:
    """Generate ONE seamlessly-looping ambient bed for a location with the
    ElevenLabs Sound-Effects API (loop=true so it repeats with no click). Returns
    the generated seconds (for cost), or 0.0 on failure — in which case the scene
    simply has no bed and the editor falls back to the clips' own audio."""
    try:
        r = requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": ambient_prompt(setting),
                  "duration_seconds": seconds, "loop": True},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"    ambient bed failed {r.status_code}: {r.text[:120]}")
            return 0.0
        with open(out_path, "wb") as f:
            f.write(r.content)
        return float(seconds)
    except Exception as e:
        print(f"    ambient bed error ({e})")
        return 0.0


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


# --- Coverage, establishing stills, reactions ------------------------------

def compose_coverage(names: list, chars_by_name: dict, setting: str,
                     room_ref: str, out_prefix: str):
    """Compose a dialogue scene's COVERAGE: a WIDE establishing two-shot + a
    close-up SINGLE of each of the (<=2) people. The singles are seeded from the
    wide (room_ref) so the room, lighting and faces match, and the 180-degree rule
    is baked in — the FIRST character sits on the LEFT looking right, the SECOND on
    the RIGHT looking left, so their singles intercut as real shot/reverse-shot.
    Returns (angles, n_new): angles = {"wide": path, <name>: single_path}, and
    n_new = how many images we actually paid to compose."""
    angles, n_new = {}, 0
    portraits = {n: os.path.join(OUT_DIR, chars_by_name[n]["file"]) for n in names}
    left = names[0]
    right = names[1] if len(names) > 1 else None

    wide = f"{out_prefix}_wide.png"
    dirn = f"wide two-shot: {descriptor(chars_by_name[left])} on the LEFT looking right"
    if right:
        dirn += f", {descriptor(chars_by_name[right])} on the RIGHT looking left"
    dirn += ", facing each other across the room"
    if compose_scene_image([portraits[n] for n in names], setting, dirn, "", wide,
                           room_ref=room_ref):
        angles["wide"] = wide
        n_new += 1

    base = angles.get("wide") or room_ref      # match the singles to the wide
    for k, n in enumerate(names):
        side = "right" if k == 0 else "left"   # keep each person's 180-rule eyeline
        sp = f"{out_prefix}_cu_{slug(n)}.png"
        shot = (f"tight vertical close-up of {descriptor(chars_by_name[n])}, head and "
                f"shoulders, looking to the {side} toward the other person just "
                f"off-camera, shallow depth of field")
        if compose_scene_image([portraits[n]], setting, shot, "", sp, room_ref=base):
            angles[n] = sp
            n_new += 1
    return angles, n_new


def still_to_clip(image_path: str, out_path: str, seconds: float = ESTABLISH_SEC) -> bool:
    """Make a short SILENT establishing clip from a still image with a slow push-in
    (Ken Burns) — no Veo, so it costs $0. A silent stereo track is added so every
    beat has audio for the editor to line up."""
    try:
        w, h = Image.open(image_path).size
        fr = max(int(seconds * 25), 1)
        vf = (f"scale={w * 2}:{h * 2},zoompan=z='min(1.0+0.05*on/{fr},1.05)':d={fr}:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps=25:s={w}x{h}")
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", image_path,
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-t", f"{seconds}", "-vf", vf, "-r", "25",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-shortest", out_path],
                       check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    establishing still-clip failed ({e})")
        return False


def add_silent_audio(video_path: str, out_path: str) -> bool:
    """Give a video-only clip (a Veo reaction rendered with audio OFF) a silent
    stereo track, so every beat carries audio for the editor."""
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path,
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                        "-shortest", out_path], check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    (could not add silent track to reaction: {e})")
        return False


def reaction_prompt(sc: dict, listener_c: dict, cue: str = "") -> str:
    """Veo prompt for a SILENT reaction beat: the listener reacts with their face
    only — no speaking, mouth closed — looking toward the other person off-camera."""
    who = descriptor(listener_c).capitalize()
    react = f" {cue.strip()}." if cue else ""
    return ("The scene is already in motion from the very first frame. ONE person "
            f"listens in silence and reacts only with their face.{react} {who} does "
            "NOT speak — mouth closed — a small natural reaction (a glance, a "
            "tightening jaw, a slow breath). They look toward the other person, off "
            f"to the side, never at the camera. {sc.get('shot', 'tight close-up')}. "
            "Moody cinematic prestige legal drama, photorealistic.")


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
    ambient_seconds = 0.0   # ElevenLabs Sound-Effects seconds (ambient beds)
    scene_images = 0        # composed images we paid for
    location_ref = {}       # setting -> first image made there (keep the room identical)
    compose_cache = {}      # (same people, same place) -> reuse that image (no re-pay)
    coverage_cache = {}     # (same people, same place) -> reuse the angle set (no re-pay)
    ambient_by_loc = {}     # setting -> looping ambient bed made there (reuse, no re-pay)
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

        # One looping ambient bed per unique location (reused across its scenes so
        # we only pay once). The editor lays this under the whole scene so the
        # audio never blinks at a cut. A failed bed just means no bed for that room.
        amb_file = ambient_by_loc.get(loc_key)
        if amb_file is None:
            amb_name = f"amb_{slug(loc_key)[:40] or 'room'}.mp3"
            secs = make_ambient(sc_setting, os.path.join(OUT_DIR, amb_name))
            amb_file = amb_name if secs > 0 else ""
            ambient_seconds += secs
            ambient_by_loc[loc_key] = amb_file
        if amb_file:
            sc["ambient"] = amb_file

        # Compose the scene image(s). We do NOT reuse a previous clip's last frame —
        # that frame is mid-talk, which makes Veo continue the wrong speaker. If the
        # SAME people were already composed in the SAME place, reuse (no re-pay), and
        # location_ref keeps the room identical even when the cast changes.
        cache_key = (frozenset(names), loc_key)
        room_ref = location_ref.get(loc_key)

        # DIALOGUE + coverage: a WIDE establishing two-shot + a close-up SINGLE of
        # each speaker, so lines can cut shot/reverse-shot. Reused per people+place.
        angles = None
        if sc.get("type") == "dialogue" and COVERAGE and names:
            cov = coverage_cache.get(cache_key)
            if cov and all(os.path.exists(p) for p in cov.values()):
                angles = cov
                print("    (reusing coverage — same people, same place, $0 saved)")
            else:
                cov, n_new = compose_coverage(
                    names, chars_by_name, sc_setting, room_ref,
                    os.path.join(OUT_DIR, f"cov_{i:02d}"))
                scene_images += n_new
                if cov.get("wide"):                    # need at least the wide to proceed
                    angles = cov
                    location_ref.setdefault(loc_key, cov["wide"])
                    coverage_cache[cache_key] = cov

        # NARRATION, coverage off, or coverage failed: one clean composed shot.
        if angles:
            start_img = angles["wide"]
        else:
            start_img = compose_cache.get(cache_key)
            if start_img and os.path.exists(start_img):
                print("    (reusing scene image — same people, same place, $0 saved)")
            else:
                start_img = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
                if compose_scene_image(portraits, sc_setting, sc.get("shot", ""),
                                       sc.get("action", ""), start_img, room_ref=room_ref):
                    scene_images += 1
                    location_ref.setdefault(loc_key, start_img)
                    compose_cache[cache_key] = start_img
        if not (start_img and os.path.exists(start_img)):
            print(f"  [{i}] skipped: no scene image could be composed.")
            continue

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
            # One beat: the lone narration clip (a talking beat — it has real
            # lip-synced speech, so the editor keeps its audio locked to its video).
            sc["beats"] = [{"file": clip_name, "kind": "narration",
                            "speaker": names[0], "silent": False}]
            print(f"  [{i}] NARRATION [{names[0]}] -> {clip_name} (Veo {secs:.0f}s, re-voiced)")
            continue

        # --- DIALOGUE: coverage beats — establishing wide + shot/reverse-shot lines
        # + silent reaction cutaways. Each stays its OWN beat; the editor joins them
        # with a continuous bed and soft cuts. Each line clip still has exactly ONE
        # speaker (rendered from that speaker's single), so the voice-swap stays clean.
        speakers = sc.get("characters", [])
        beats = []

        # 0) ESTABLISHING — a short FREE still-zoom on the wide two-shot, so we open
        #    on the geography (who is where) before cutting to singles. $0, no Veo.
        if angles and angles.get("wide"):
            est_name = f"beat_{i:02d}_00.mp4"
            if still_to_clip(angles["wide"], os.path.join(OUT_DIR, est_name)):
                beats.append({"file": est_name, "kind": "establishing",
                              "speaker": None, "silent": True})

        lines = sc.get("dialogue", [])
        for j, d in enumerate(lines, 1):
            spk = d.get("character")
            spk_c = chars_by_name.get(spk, {})
            listener = next((n for n in names if n != spk), None)
            listener_descs = [descriptor(chars_by_name[listener])] if listener else []

            # 1) LINE beat — from the SPEAKER's single (so lines cut shot/reverse-shot);
            #    fall back to the wide/two-shot if a single is missing.
            line_img = (angles or {}).get(spk) or start_img
            raw = os.path.join(OUT_DIR, f"_raw_{i:02d}_{j:02d}.mp4")
            beat_name = f"beat_{i:02d}_{j:02d}.mp4"
            beat_path = os.path.join(OUT_DIR, beat_name)
            secs = make_veo_clip(
                line_img, line_prompt(sc, spk_c, listener_descs, d["line"], d.get("emotion", "")),
                veo_duration(d["line"]), raw)
            if secs == 0.0 or not os.path.exists(raw):
                print(f"    line {j} skipped (Veo could not generate it).")
                continue
            veo_seconds += secs
            sts_seconds += revoice(raw, spk_c.get("voice_id", ""), beat_path)
            if os.path.exists(raw):
                os.remove(raw)
            beats.append({"file": beat_name, "kind": "line",
                          "speaker": spk, "silent": False})

            # 2) REACTION beat — after a non-final line, cut to the LISTENER reacting
            #    in silence (a cutaway that hides the seam before the next speaker).
            #    Rendered with audio OFF (no invented voice), then given a silent track.
            react_img = (angles or {}).get(listener) if listener else None
            if REACTIONS and react_img and j < len(lines):
                rraw = os.path.join(OUT_DIR, f"_rraw_{i:02d}_{j:02d}.mp4")
                rbeat = f"beat_{i:02d}_{j:02d}r.mp4"
                rsecs = make_veo_clip(
                    react_img, reaction_prompt(sc, chars_by_name.get(listener, {}),
                                               d.get("reaction", "")),
                    REACTION_DUR, rraw, generate_audio=False)
                if rsecs > 0 and os.path.exists(rraw) and \
                        add_silent_audio(rraw, os.path.join(OUT_DIR, rbeat)):
                    veo_seconds += rsecs
                    beats.append({"file": rbeat, "kind": "reaction",
                                  "speaker": listener, "silent": True})
                if os.path.exists(rraw):
                    os.remove(rraw)

        if not any(b["kind"] in ("line", "narration") for b in beats):
            print(f"  [{i}] DIALOGUE skipped: no lines.")
            continue
        sc["beats"] = beats
        nl = sum(1 for b in beats if b["kind"] == "line")
        nr = sum(1 for b in beats if b["kind"] == "reaction")
        print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {nl} lines, {nr} reactions"
              f"{' + establishing' if angles else ''} ({'wide+singles' if angles else 'two-shot'})")

    # --- Cost: Veo clips + voice swap + ambient beds + composed images ---
    veo_cost = veo_seconds * costs.VEO_FAST_AUDIO_PER_SEC
    sts_cost = sts_seconds * costs.ELEVEN_STS_PER_SEC
    sfx_cost = ambient_seconds * costs.ELEVEN_SFX_PER_SEC
    image_cost = scene_images * costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
    clip_cost = veo_cost + sts_cost + sfx_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Veo 3.1 fast ({veo_seconds:.0f}s) + voice swap "
                 f"({sts_seconds:.0f}s) + {scene_images} images + "
                 f"{ambient_seconds:.0f}s ambient",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated output/analysis.json with the scene beats. "
          f"({scene_images} scene images, {len(ambient_by_loc)} ambient beds)")
    costs.show(f"{len(scenes)} scenes (Veo {veo_seconds:.0f}s + voice swap "
               f"{sts_seconds:.0f}s + {scene_images} images + ambient)", clip_cost)


if __name__ == "__main__":
    main()
