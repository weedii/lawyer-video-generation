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
# 1080p costs the SAME per second as 720p on Veo fast, so we take the sharper one for
# free. (Only 4K costs more.)
VEO_RES = "1080p"

# ONE shared visual look, dropped into EVERY prompt — the character sheet, the composed
# scene image, and every Veo clip. Reusing the exact same palette/grain/lens wording is
# what makes separate generations read as a single film instead of clips from different
# cameras, and it softens the jump between scenes. Kept in one place so it can never
# drift out of sync between the steps.
STYLE = ("shot on 35mm film, muted teal-and-amber palette, soft cinematic grain, "
         "shallow depth of field, moody prestige legal-drama lighting, photorealistic")

# DRAFT mode (run with DRAFT=1 in the environment) renders cheap for iteration: no audio
# and the shortest clip length, so you can check framing, identity and motion without
# paying for the audio pass or full duration. A real run leaves it off, so we get native
# voices + lip-sync at full length.
DRAFT = os.getenv("DRAFT") == "1"

# --- Coverage + reactions (DISABLED — see note) -----------------------------
# The IDEA: COVERAGE = compose a WIDE two-shot + a close-up SINGLE of each speaker
# and render each line from the speaker's single, so lines cut shot/reverse-shot;
# REACTIONS = cut to a short silent listener clip between lines.
#
# WHY THEY ARE OFF: a close-up shows almost no room, so Nano IGNORES the "keep this
# location" reference and reinvents the background (and sometimes drifts the face)
# — the speaker's close-up landed in a different place (and wrong look) than the
# wide. That broke consistency, so we render dialogue from the ONE consistent
# two-shot instead and get the smoothness from the EDITOR (continuous sound bed +
# grade + trimmed pauses). The code below stays, behind these flags, so we can
# re-enable it once we have a close-up method that actually holds the location
# (e.g. a real crop of the wide, or a start+end-frame model).
COVERAGE = False          # compose singles + establishing wide (shot/reverse-shot)
REACTIONS = False         # insert silent listener reaction cutaways between lines
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


def identity_lock(c: dict) -> str:
    """The character's fixed identity sentence (hair, build, KEY CLOTHING COLOUR, key
    prop), carried unchanged into every prompt. This is the text anchor that keeps a
    person looking the same shot to shot, on top of their reference photo — and because
    it names their clothing colour, it stops the video model from recolouring their
    outfit when the camera swings to a new angle (the grey suit that turned blue). Falls
    back to the role handle if analyze produced no lock, so there is always an anchor."""
    lock = (c.get("lock") or "").strip()
    return lock if lock else descriptor(c)


# --- Scene image: put the characters together in the setting ---------------

def compose_scene_image(portrait_paths: list, setting: str, shot: str,
                        action: str, out_path: str, room_ref: str = None,
                        people: list = None) -> bool:
    """Compose the given character portraits into ONE upright vertical scene image
    (Nano Banana Pro edit). Keeps their exact faces. Returns True on success.

    Composed VERTICALLY (people in front, room rising behind/above) so the model
    does not rotate a wide layout sideways. room_ref keeps the SAME room when the
    cast changes (so a later shot in the same place isn't a different building).
    people = each character's identity-lock sentence, IN THE SAME ORDER as the
    portraits, so we can tie each reference photo to one named person."""
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
    # Each person reference is now an 8-shot sheet (the same person from several
    # angles). Without this the compositor can read the sheet literally and paste the
    # grid or spawn duplicates, so state that each sheet is one single identity.
    sheet_note = (" Each person reference is a multi-angle character sheet of ONE "
                  "individual — render that single person, never the grid layout and "
                  "never a duplicate of anyone.")
    # Tie each reference photo to a named identity ("the first person is <lock>, the
    # second is <lock>"). Naming each reference separately stops the compositor from
    # blending the two people into one, and repeating each person's clothing colour
    # here keeps their outfit from being recoloured. Belt-and-braces with the photos.
    ordinals = ["first", "second", "third", "fourth"]
    whois = ""
    if people:
        labels = [f"the {ordinals[i] if i < len(ordinals) else 'next'} person is {p}"
                  for i, p in enumerate(people)]
        whois = (" " + "; ".join(labels) +
                 ". Keep each person's clothing and its exact colour unchanged.")
    if room_ref and os.path.exists(room_ref):
        urls.append(fal_client.upload_file(room_ref))   # room reference goes LAST
        prompt = (
            f"The first {n} image(s) are people; the LAST image is a room. Place "
            f"those people together inside the SAME room shown in the last image — "
            f"keep that room's EXACT architecture, windows, wood panelling, "
            f"furniture, lighting and colour so it is unmistakably the identical "
            f"location. {only}{face}{sheet_note}{whois} {action}. Keep each person's exact "
            f"face and clothing. {shot}. Vertical 9:16 portrait, upright: the people stand/sit "
            f"in the foreground with heads near the TOP of the frame, the room rising "
            f"behind and above them. {STYLE}. NOT rotated, NOT sideways, NOT landscape."
        )
    else:
        prompt = (
            f"Put these people together in ONE cinematic shot inside {setting}. "
            f"{only}{face}{sheet_note}{whois} {action}. Keep their exact faces and clothing. "
            f"{shot}. Vertical 9:16 portrait, upright: the people stand/sit in the foreground "
            f"with heads near the TOP of the frame, the room rising behind and above them. "
            f"{STYLE}. NOT rotated, NOT sideways, NOT landscape."
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


def _line_windows(lines: list):
    """Give each dialogue line its own time slice (~2.5 words/sec, min 1.8s so even a
    short line has room to land) plus the running total, clamped to Veo's 8s ceiling.
    These slices become an explicit timeline in the prompt. Without a per-speaker time
    budget Veo tries to voice both lines at once, rushes the hand-off, and fills the
    seam with a garbled beat where one person's voice comes out of the other's mouth.
    A timeline hands each speaker a clear window instead."""
    wins, t = [], 0.0
    for d in lines:
        w = len((d.get("line") or "").split())
        dur = max(w / 2.5, 1.8)
        wins.append([t, t + dur])
        t += dur
    # If the lines overrun 8s, squeeze every window proportionally so the last line
    # still gets a slot instead of being cut off at the ceiling.
    if t > 8.0:
        scale = 8.0 / t
        wins = [[s * scale, e * scale] for s, e in wins]
    return wins, min(t, 8.0)


def scene_veo_duration(lines: list) -> str:
    """Veo length for a whole dialogue scene: match the summed line windows so the
    clip is only as long as the actual speech. Leaving an empty tail is what lets Veo
    invent extra mumbling to fill it, so we size tight. Clamped to Veo's 4/6/8s."""
    _, total = _line_windows(lines)
    return "4s" if total <= 4.5 else "6s" if total <= 6.5 else "8s"


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


def crop_single(wide_path: str, side: str, out_path: str) -> bool:
    """Cut a SINGLE-person shot out of the composed wide two-shot with pure ffmpeg
    (no AI, so nothing can drift): zoom into the speaker's HALF so only ONE person
    is in frame, keeping the REAL background from the wide. side = 'left'/'right'.
    This is what guarantees Veo animates the CORRECT person in the CORRECT place —
    with two faces in a two-shot Veo sometimes lip-syncs the wrong one; here there
    is only one face to animate. Keeps 9:16 (crops a 1/ZOOM window of the same
    aspect) and biases DOWN to where seated faces sit."""
    Z = 1.6
    x = "0" if side == "left" else f"iw-iw/{Z}"
    vf = f"crop=iw/{Z}:ih/{Z}:{x}:ih-ih/{Z},scale=1080:1920"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", wide_path, "-vf", vf,
                        "-frames:v", "1", out_path], check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    crop single failed ({e})")
        return False


def two_shot_shot(names: list, chars_by_name: dict, base_shot: str) -> str:
    """Build the wide-two-shot camera line with FORCED left/right positions, so we
    know which side to crop for each speaker. First cast member = LEFT, second =
    RIGHT. Keeps the model from placing them in a random order."""
    left = descriptor(chars_by_name.get(names[0], {}))
    parts = [f"medium two-shot, {left} seated on the LEFT"]
    if len(names) > 1:
        parts[0] += f", {descriptor(chars_by_name[names[1]])} seated on the RIGHT"
    parts[0] += ", facing each other across a table"
    if base_shot:
        parts.append(base_shot)
    return ". ".join(parts)


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

def single_line_prompt(sc: dict, speaker_c: dict, line: str, emotion: str) -> str:
    """Veo prompt for a line rendered from a CROPPED SINGLE (only the speaker is in
    frame). They talk to the other person just OFF-camera to the side — not to the
    lens — so we get one correct mouth moving and no wrong-person lip-sync."""
    who = descriptor(speaker_c).capitalize()
    tone = f", {emotion.strip()}," if emotion else ""
    return ("The scene is already in motion from the very first frame. ONE person is "
            f"in frame. {who}{tone} looks toward the other person just off-camera to "
            f"the side — NOT at the camera — and says these exact words: \"{line}\". "
            "Only this person's mouth moves to these words, with natural expression and "
            f"small head movement. {sc.get('shot', 'tight medium shot')}. Moody "
            "cinematic prestige legal drama, photorealistic.")


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


def _reply_cue(c: dict) -> str:
    """'She replies' / 'He replies'. The word 'replies' signals to Veo that the next
    line is a consecutive response, not simultaneous speech — which is what keeps the
    two speakers from overlapping into one garbled voice."""
    return "She replies" if character_gender(c) == "female" else "He replies"


def build_scene_prompt(sc: dict, chars_by_name: dict) -> str:
    """One prompt for a WHOLE dialogue scene as a single continuous Veo clip, written
    as an explicit TIMELINE. Each line gets its own time window and a framing change —
    a medium two-shot for the first line, a reverse over-the-shoulder on the reply —
    so the shot cuts between the two faces INSIDE one generation (no glue seam) and
    each speaker owns a clear slice of time. The per-line windows plus the 'replies'
    hand-off are what stop Veo from voicing both lines at once and mumbling a garbled
    bridge between them. Delivery cue and micro-expression ride inside each line so
    the faces perform."""
    lines = sc.get("dialogue", [])
    speakers = sc.get("characters", [])
    who = {n: descriptor(chars_by_name.get(n, {})) for n in speakers}
    wins, total = _line_windows(lines)
    # Fit the per-line windows exactly into the clip's chosen length, so the timeline
    # never runs past the end (which would cut the last word) or leave an empty tail
    # for Veo to fill with invented sound.
    veo_secs = int(scene_veo_duration(lines)[:-1])
    if total > 0:
        scale = veo_secs / total
        wins = [[s * scale, e * scale] for s, e in wins]
    # A different framing per line so one continuous take still reads as real coverage.
    shots = ["Medium two-shot, slow push-in",
             "Reverse over-the-shoulder onto the other person",
             "Tighter two-shot, favouring the speaker"]
    rows = []
    for j, d in enumerate(lines):
        s, e = wins[j]
        spk_name = d.get("character", "")
        spk = who.get(spk_name, "the other person").capitalize()
        tone = f" ({d['emotion'].strip()})" if d.get("emotion") else ""
        lead = (f"{spk} says{tone}" if j == 0
                else f"{_reply_cue(chars_by_name.get(spk_name, {}))}{tone}")
        rows.append(f"{s:.1f}-{e:.1f}s: {shots[min(j, len(shots) - 1)]} - "
                    f"{lead}: \"{d.get('line', '')}\".")
    # CAST block: pin each speaker to their locked identity (hair, build, CLOTHING
    # COLOUR) so the reverse angle can't redraw the wrong face or recolour an outfit.
    cast = " ".join(f"{who.get(n, 'a person').capitalize()} = "
                    f"{identity_lock(chars_by_name.get(n, {}))}." for n in speakers)
    # The scene's blocking, so the shot has a concrete physical action, not just heads.
    action = (sc.get("action") or "").strip().rstrip(".")
    action_line = f" {action}." if action else ""
    return (
        # Lead with motion so Veo doesn't open on a frozen staring frame.
        "A continuous cinematic scene, already in motion from the first frame — two "
        "people mid-conversation, facing EACH OTHER, never the camera. "
        f"{STYLE}, natural room ambience, no music.\n"
        f"CAST: {cast}\n"
        "TIMELINE:\n" + "\n".join(rows) +
        "\nOnly the person whose line it is moves their mouth; the other listens and "
        f"reacts.{action_line} Nuanced facial micro-expressions, real weighty movement and "
        "object physics, each person's face and clothing colour identical throughout, "
        "movie-level subtlety."
    )


def narration_prompt(sc: dict, lead_c: dict) -> str:
    """Veo prompt for a memoir narration beat: the lone protagonist performs it
    straight to camera, first person."""
    who = descriptor(lead_c).capitalize() if lead_c else "The narrator"
    # Same identity lock as the dialogue scenes, so the narrator is unmistakably the
    # same person (same face + clothing colour) whenever he appears between scenes.
    lock = identity_lock(lead_c) if lead_c else ""
    lock_line = f" ({lock})" if lock else ""
    return (
        "The scene is already in motion from the very first frame. ONE person is "
        "ALONE in the shot. "
        f"{sc.get('shot', 'slow push-in on a lone figure')}. {sc.get('action', '')}. "
        f"{who}{lock_line} looks directly INTO the camera and tells us their own story, "
        f"first person, like a memoir confession: \"{sc.get('narration', '')}\". Nobody "
        f"else is present. {STYLE}."
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
            amb_path = os.path.join(OUT_DIR, amb_name)
            # Resume guard: if a previous run already made this room's bed, reuse the
            # file for free instead of re-paying ElevenLabs.
            if os.path.exists(amb_path):
                amb_file = amb_name
                print(f"    (reusing ambient bed {amb_name} — already on disk, $0)")
            else:
                secs = make_ambient(sc_setting, amb_path)
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
                if os.path.exists(start_img):
                    # Resume guard: this scene image was composed in a previous run —
                    # reuse it, don't re-pay Nano Banana.
                    print(f"    (reusing scene image {os.path.basename(start_img)} "
                          "— already on disk, $0)")
                    location_ref.setdefault(loc_key, start_img)
                    compose_cache[cache_key] = start_img
                else:
                    # For DIALOGUE, seat the two people as a clean facing two-shot;
                    # narration is one person. The identity locks (in the same order as
                    # the portraits) name each reference and pin each person's clothing
                    # colour so the composite can't blend or recolour them.
                    compose_shot = (two_shot_shot(names, chars_by_name, sc.get("shot", ""))
                                    if sc.get("type") == "dialogue" and len(names) >= 2
                                    else sc.get("shot", ""))
                    locks = [identity_lock(chars_by_name[n]) for n in names]
                    if compose_scene_image(portraits, sc_setting, compose_shot,
                                           sc.get("action", ""), start_img,
                                           room_ref=room_ref, people=locks):
                        scene_images += 1
                        location_ref.setdefault(loc_key, start_img)
                        compose_cache[cache_key] = start_img
        if not (start_img and os.path.exists(start_img)):
            print(f"  [{i}] skipped: no scene image could be composed.")
            continue

        # --- NARRATION: one Veo clip, protagonist to camera, then re-voice ---
        if sc.get("type") == "narration":
            # Resume guard: if a previous run already rendered + re-voiced this
            # narration clip, reuse it for free instead of re-paying Veo.
            if os.path.exists(clip_path):
                sc["beats"] = [{"file": clip_name, "kind": "narration",
                                "speaker": names[0], "silent": False}]
                print(f"  [{i}] NARRATION [{names[0]}] -> {clip_name} "
                      "(reused, already on disk, $0)")
                continue
            lead_c = chars_by_name.get(names[0], {})
            raw = os.path.join(OUT_DIR, f"_raw_{i:02d}.mp4")
            # DRAFT: shortest length + no audio, to preview the shot cheaply.
            dur = "4s" if DRAFT else veo_duration(sc.get("narration", ""))
            secs = make_veo_clip(start_img, narration_prompt(sc, lead_c), dur, raw,
                                 generate_audio=not DRAFT)
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

        # --- DIALOGUE: render the WHOLE scene as ONE continuous Veo clip ---
        # Both people act and talk in a single generation, so the hand-off from one
        # line to the next happens inside the clip with Veo's native lip-sync — no
        # glue seam between separate clips, which is what made past conversations feel
        # chopped. The two speakers therefore share ONE clip; Veo also invents their
        # voices here. We keep those native voices for dialogue because pulling two
        # speakers back apart to swap in our cloned voices would need per-speaker
        # diarisation (a later upgrade). The recurring NARRATOR is still re-voiced
        # above, so the lead's voice stays consistent across every video.
        speakers = sc.get("characters", [])
        if not sc.get("dialogue"):
            print(f"  [{i}] DIALOGUE skipped: no lines.")
            continue

        # Resume guard: a finished scene clip on disk is reused for free.
        if os.path.exists(clip_path):
            sc["beats"] = [{"file": clip_name, "kind": "dialogue",
                            "speaker": None, "silent": False}]
            print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {clip_name} "
                  "(reused, already on disk, $0)")
            continue

        # DRAFT: shortest length + no audio, to preview framing/identity cheaply.
        dur = "4s" if DRAFT else scene_veo_duration(sc.get("dialogue", []))
        secs = make_veo_clip(start_img, build_scene_prompt(sc, chars_by_name),
                             dur, clip_path, generate_audio=not DRAFT)
        if secs == 0.0 or not os.path.exists(clip_path):
            print(f"  [{i}] DIALOGUE skipped: Veo could not generate it.")
            continue
        veo_seconds += secs
        # One beat = the whole scene. It carries real lip-synced speech, so the editor
        # keeps its audio locked to its own video (no sliding across this cut).
        sc["beats"] = [{"file": clip_name, "kind": "dialogue",
                        "speaker": None, "silent": False}]
        print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {clip_name} "
              f"(one continuous clip, Veo {secs:.0f}s, native voices)")

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
