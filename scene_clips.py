"""Render one cinematic CLIP per SCENE — the VIDEO step (memoir VOICEOVER pipeline).

The whole video is a first-person MEMOIR: ONE voice — the protagonist's — narrates
every scene, over cinematic silent footage. There is NO synced character dialogue and
NO lip-sync anywhere, which is exactly what removes the whole class of bugs we hit
before (wrong voice on the wrong face, garbled two-person clips, geography jumps when
composing reverse angles). Every scene, dialogue or narration, is built the same way:

  1. Compose ONE image of the scene's on-screen cast in the setting (Nano Banana 2
     edit, from their locked portraits so faces stay the same). Narration scenes are
     the lone protagonist; dialogue scenes hold everyone the beat puts in the room.
  2. Render ONE **silent** Seedance 1.5 pro clip of that image — the characters act and
     (for dialogue) silently mouth their lines; the protagonist in narration is
     contemplative, mouth closed. Silent = the cheaper $0.026/s rate and nothing to
     lip-sync.
  3. Lay this scene's VOICEOVER on top. The voiceover was MADE in the previous step
     (audio_maker) and is recorded on the scene as sc["vo_file"]/["vo_seconds"]; here we
     just read it, size the video to it, and mux it over the silent clip (no sync).

The scene IMAGES are composed one at a time (step 1 stays serial so each room's anchor is
ready before the next scene reuses it), but the Seedance CLIPS (step 2) all render in
PARALLEL — up to MAX_PARALLEL_RENDERS at once — instead of one after another, so a 9-scene
video's render step takes about as long as the slowest single clip rather than the sum of
them all. Same cost, far less waiting.

So this step only pays for the VIDEO: Seedance (the silent clips) and Nano Banana 2 (the
composed scene images). The voiceover, ambience beds and music are all made and paid for
in audio_maker (step 6); the editor (assemble.py) later joins the beats with those beds,
ducked music and location cards. One voice across the whole film = perfect voice
consistency, zero lip-sync risk.

Why Seedance: it holds our AI-invented faces (Veo's likeness filter refused them on
~half of clips), runs on fal's crash-safe queue, and is cheap — and here we only ever
use its SILENT tier.

Usage:
    python scene_clips.py     (run audio_maker.py first, so the voiceovers exist)

Reads:  output/analysis.json   (characters w/ portraits, script scenes, per-scene vo_file)
        + output/vo_XX.mp3 (the voiceovers made by audio_maker)
Output: output/clip_XX.mp4 (one per scene); writes each scene's ordered scene["beats"] list.
Cost:   Seedance 1.5 pro (fal) — $0.026/s at 720p SILENT (every scene clip + detail
        inserts) + $0.08 per composed scene image (Nano Banana 2, 1K). The audio cost lives
        in audio_maker. No lip-sync.
"""
import os
import sys
import json
import re
import time
import math
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import fal_client
from PIL import Image
from dotenv import load_dotenv
import costs

load_dotenv()
# Nano Banana (scene composition), Seedance (video) and ElevenLabs (re-voice) all run on
# fal / ElevenLabs, so both keys are always required.
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVEN_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty (needed to re-voice into our voices).")

OUT_DIR = "output"
# The SCENE video model — our biggest cost. Seedance 1.5 pro (image-to-video) replaced
# Veo 3.1: Veo's likeness filter refused our AI-invented faces on ~half of clips (a
# Google policy, not promptable-around), and it cost $0.10-0.15/s. Seedance holds the
# same two faces, does native lip-sync, is NOT blocked, and is ~$0.052/s — so a whole
# video comes in under budget. It runs on fal's own queue, so all the crash-safe
# submit/poll/resume code below is the SAME machinery, just pointed at a new model.
VIDEO_MODEL = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"
# Nano Banana 2 (fal) = Google Gemini 3.1 Flash Image — composes the cast into one scene
# image. It REPLACED non-pro ($0.039), which shipped anatomically broken people: one scene
# came back with a third arm grafted onto a character (arms folded AND a second pair of
# forearms on the desk doing another character's action) and another character rendered as
# the wrong gender. The video model cannot fix that — Seedance animates whatever still it is
# handed — so the image has to be right before we pay to animate it. On a 9-scene bake-off
# NB2 was clean on both, and it also beat PRO, which duplicated a character in a 2-person
# shot. We send NO "resolution": 1K is the default and cheapest tier ($0.08), and 2K would
# cost 1.5x for detail that Seedance at 720p throws away.
SCENE_EDIT_MODEL = "fal-ai/nano-banana-2/edit"    # compose chars into one shot (1K default)
IMAGE_MODEL = "fal-ai/nano-banana-2"              # text-to-image (detail with no room ref)
# PRO fallback for the compose step ONLY. The cheap tier sometimes returns NO image on a
# hard 2-person shot — it silently gives up (a "no_media_generated" error, NOT a content
# block: the exact same prompt succeeds on Pro). When that happens we retry that ONE image
# on Pro, so we pay Pro's higher price only on the few hard scenes that need it. This is a
# SECOND OPINION from a different model, not an upgrade — Pro tested WORSE than NB2 on cast
# duplication; it is here so a failed compose still produces a picture.
SCENE_EDIT_MODEL_PRO = "fal-ai/nano-banana-pro/edit"
_pro_fallbacks = 0                                 # count of composes that fell back to Pro (for honest cost)
# The narrator VOICEOVER: we make the words ourselves with ElevenLabs text-to-speech and lay
# them OVER the silent clip (no lip-sync — the characters are never heard). This is the only
# voice in the video.
TTS_MODEL = "eleven_multilingual_v2"                # ElevenLabs text-to-speech (the voiceover)
# Voice settings tuned for CLARITY — the narration is the ONLY voice in the video and it's
# watched while scrolling, so it must be easy to catch. Steady (higher stability) so words
# don't slur, and a touch slower (speed 0.95) so each line lands.
VOICE_SETTINGS = {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0, "speed": 0.95}
# LEGACY (unused): the old on-screen-dialogue path re-dubbed Seedance mouths onto our audio
# with these lip-sync models. The voiceover style dropped all lip-sync; kept for reference.
SYNC_MODEL = "fal-ai/sync-lipsync/v2"              # legacy: 2-face active-speaker sync
LATENTSYNC_MODEL = "fal-ai/latentsync"            # legacy: single-face sync
# 720p is Seedance's balanced tier and the price we budgeted ($0.052/s with audio); the
# editor upscales the final cut to 1080x1920. Seedance IGNORES the input image's shape
# and defaults to 16:9 landscape, so aspect_ratio="9:16" MUST be sent on every call
# (verified: without it a vertical start image still came out landscape).
VIDEO_RES = "720p"
VIDEO_ASPECT = "9:16"

# How many Seedance clips to render AT THE SAME TIME. Each clip is an independent fal job
# (submit, then poll), so they can all run in parallel instead of one after another — the old
# serial loop is why a 9-scene video's render step took ~23 minutes; in parallel it takes about
# as long as the single slowest clip (~2-3 min). The default is 9 because a video is 7-9 scenes,
# so every clip renders in ONE wave (a cap of 6 left 2 clips waiting for a second round, which
# roughly doubled the render time). Lower it to 1 (fully serial) if fal ever rate-limits.
# Override with the MAX_PARALLEL_RENDERS env var without touching the code.
MAX_PARALLEL_RENDERS = int(os.getenv("MAX_PARALLEL_RENDERS", "9"))

# ONE shared visual look, dropped into EVERY prompt — the character sheet, the composed
# scene image, and every Seedance clip. Reusing the exact same palette/grain/lens wording is
# what makes separate generations read as a single film instead of clips from different
# cameras, and it softens the jump between scenes. Kept in one place so it can never
# drift out of sync between the steps.
STYLE = ("shot on 35mm film, muted teal-and-amber palette, soft cinematic grain, "
         "shallow depth of field, moody prestige legal-drama lighting, photorealistic")

# DRAFT mode (run with DRAFT=1 in the environment) renders cheap for iteration: the shortest
# clip length and no voiceover, so you can check framing, identity and motion without paying
# for full-length clips or the TTS. A real run leaves it off, so each scene gets its full-length
# silent clip with the narrator's voiceover laid over it.
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
REACTION_DUR = "5s"       # Seedance's shortest safe length; the editor caps silent beats short
# DETAIL INSERT: at each NEW location we open on a short, silent, face-free shot of one
# object that says where we are (a gavel, a nameplate, a case bundle) — the modern
# replacement for the establishing wide. It orients the viewer before the scene starts,
# and because it has no faces nothing can drift. One Nano image ($0.08) + one ~5s silent
# Seedance clip (~$0.13) per location, cached so we pay once per place. This is real footage,
# not a frozen still — that is why the old still-zoom establishing beat was removed.
# DISABLED for now (set back to True to re-enable). In the narration style the VOICEOVER
# already introduces every new place and person (and the location cards label the place on
# screen), so the detail inserts became a redundant fourth cue — they mostly added visual
# variety, not orientation. Turning them off saves ~$0.85 per video (5 Nano images + 5 silent
# Seedance clips on a typical run) and makes the cut tighter. Kept in the code (make_detail_insert,
# compose_detail_image, detail_insert_prompt below) so they can be switched on again later.
DETAIL_INSERTS = False
DETAIL_DUR = "5s"         # Seedance's shortest safe length; the editor trims it to a ~1.2s glance


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

def sheet_ref(sheet_path: str, name: str) -> str:
    """Crop the FRONT FULL-BODY cell out of a character's 4-column x 2-row reference sheet
    and return that single clean photo, to be used as the compose reference instead of the
    whole grid. Cached per character (ref_<name>.png).

    WHY: feeding the entire GRID sheet into nano-banana-2/edit made the compositor ECHO the
    layout — a one-person narration shot came back as TWO stacked panels of the same man
    (walking up top, standing arms-crossed below). A single, non-grid photo has no layout
    to copy, so the person is composed exactly once. We take the top-left cell because the
    sheet's top row is the full-body views and column one is the FRONT view — so that cell
    carries face + build + wardrobe together, the best single identity anchor."""
    ref = os.path.join(OUT_DIR, f"ref_{slug(name)}.png")
    if os.path.exists(ref) and os.path.getsize(ref) > 0:
        return ref
    try:
        im = Image.open(sheet_path)
        w, h = im.size
        im.crop((0, 0, w // 4, h // 2)).save(ref)   # column 0, row 0 = front full-body
        return ref
    except Exception as e:
        print(f"    (could not crop reference for {name}: {e}); using full sheet")
        return sheet_path


def compose_scene_image(portrait_paths: list, setting: str, shot: str,
                        action: str, out_path: str, room_ref: str = None,
                        people: list = None) -> bool:
    """Compose the given character portraits into ONE upright vertical scene image
    (Nano Banana non-pro edit). Keeps their exact faces. Returns True on success.

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
    # Each reference is now a SINGLE cropped photo (see sheet_ref), not the 8-shot grid —
    # but keep a hard anti-duplicate instruction so the compositor can never split the
    # frame into repeated panels of the same person (the stacked-twice bug this replaced).
    sheet_note = (" Each person reference is ONE photo of ONE individual — render that "
                  "person exactly once; never duplicate, clone, mirror, split or repeat "
                  "anyone, and never divide the frame into panels or rows.")
    # Scene images kept coming back with words baked in (a firm name on the glass, a book
    # reading "FIRM BRANDED"). Ban all text — it looks fake and, because this image is then
    # fed as a reference into the video model, any text can bleed onward into the clip.
    no_text = (" Render NO text, words, letters, numbers, captions, labels, signage, logo "
               "or watermark anywhere in the image.")
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
            f"behind and above them. {STYLE}.{no_text} NOT rotated, NOT sideways, NOT landscape."
        )
    else:
        prompt = (
            f"Put these people together in ONE cinematic shot inside {setting}. "
            f"{only}{face}{sheet_note}{whois} {action}. Keep their exact faces and clothing. "
            f"{shot}. Vertical 9:16 portrait, upright: the people stand/sit in the foreground "
            f"with heads near the TOP of the frame, the room rising behind and above them. "
            f"{STYLE}.{no_text} NOT rotated, NOT sideways, NOT landscape."
        )
    # One compose call: subscribe, download, and say whether it came out upright.
    # Pro takes a "resolution" (2K); non-pro doesn't — extra args differ by model.
    def _compose_once(model: str, prompt_text: str, extra: dict) -> bool:
        r = fal_client.subscribe(
            model,
            arguments={"image_urls": urls, "prompt": prompt_text,
                       "aspect_ratio": "9:16", "num_images": 1, **extra},
            with_logs=False,
        )
        url = r["images"][0]["url"]
        with open(out_path, "wb") as f:
            f.write(requests.get(url).content)
        return is_portrait(out_path)

    # 1) Cheap tier first: up to 2 non-pro attempts (the 2nd nudges it to stay upright).
    for attempt in range(1, 3):
        p = prompt if attempt == 1 else prompt + " CRITICAL: upright vertical frame."
        try:
            if _compose_once(SCENE_EDIT_MODEL, p, {}):
                return True
            print(f"    scene image came out landscape; retrying ({attempt})")
        except Exception as e:
            print(f"    compose attempt {attempt} (non-pro) failed ({e}); retrying in 5s ...")
            time.sleep(5)
    # 2) Pro fallback: non-pro gave nothing usable, so pay for ONE Pro render of this hard
    #    image. Pro bills whether the frame is upright or not, so we count the fallback as
    #    soon as the call returns (an exception means nothing was billed).
    global _pro_fallbacks
    try:
        print("    non-pro gave no usable image; falling back to Nano Banana PRO for this one ...")
        ok = _compose_once(SCENE_EDIT_MODEL_PRO,
                           prompt + " CRITICAL: upright vertical frame.",
                           {"resolution": "2K"})
        _pro_fallbacks += 1
        if ok:
            print("    Pro fallback produced the scene image.")
            return True
    except Exception as e:
        print(f"    Pro fallback also failed ({e})")
    return os.path.exists(out_path)


# --- Detail (establishing) image: an object, no people --------------------

def compose_detail_image(detail: str, setting: str, room_ref: str, out_path: str) -> bool:
    """Compose a face-free ESTABLISHING detail of a location — one object that says
    where we are (a gavel, a brass nameplate, a stack of case files). This is the modern
    replacement for the establishing wide: it orients the viewer and, because it has NO
    faces, nothing can drift. room_ref (the first image shot in this location) keeps the
    detail in the SAME room; without it we compose the object from the setting text."""
    obj = (detail or "a telling object").strip().rstrip(".")
    place = (setting or "the room").strip().rstrip(".")
    common = (f"A cinematic extreme close-up of {obj} in {place}. NObody in frame — no "
              f"person, no face, no hands, no figure, just the object and its surroundings. "
              f"{STYLE}. Vertical 9:16 portrait, upright, shallow depth of field.")
    try:
        if room_ref and os.path.exists(room_ref):
            # Anchor to the real room so the detail sits in the same location as the scene.
            url = fal_client.upload_file(room_ref)
            r = fal_client.subscribe(
                SCENE_EDIT_MODEL,
                arguments={"image_urls": [url],
                           "prompt": (f"Using the attached image only as the ROOM, show {common} "
                                      f"Keep that room's exact look; place the object naturally in it."),
                           "aspect_ratio": "9:16", "num_images": 1},
                with_logs=False,
            )
        else:
            r = fal_client.subscribe(
                IMAGE_MODEL,       # no room to anchor to -> compose the object from text
                arguments={"prompt": common, "aspect_ratio": "9:16", "num_images": 1},
                with_logs=False,
            )
        with open(out_path, "wb") as f:
            f.write(requests.get(r["images"][0]["url"], timeout=120).content)
        return os.path.exists(out_path) and is_portrait(out_path)
    except Exception as e:
        print(f"    detail image failed ({e})")
        return False


def make_detail_insert(i: int, detail: str, setting: str, room_ref: str):
    """Build the establishing detail beat for a new location: compose a face-free object
    image (Nano) and animate it as a short SILENT Seedance clip. Returns
    (beat_or_None, images_paid, video_seconds). Resume-guarded on both the image and the
    clip, so a re-run collects finished work for free. A failure at either step returns
    no beat — the scene simply opens on its dialogue instead."""
    if not DETAIL_INSERTS or not detail:
        return None, 0, 0.0
    img = os.path.join(OUT_DIR, f"detail_{i:02d}.png")
    clip = f"insert_{i:02d}.mp4"
    clip_path = os.path.join(OUT_DIR, clip)
    beat = {"file": clip, "kind": "insert", "speaker": None, "silent": True}

    if os.path.exists(clip_path):                      # already rendered on a prior run
        print(f"    (reusing detail insert {clip} — already on disk, $0)")
        return beat, 0, 0.0

    images_paid = 0
    if not os.path.exists(img):
        if not compose_detail_image(detail, setting, room_ref, img):
            return None, 0, 0.0
        images_paid = 1
    # Silent (generate_audio=False): a detail shot has no voice, and the editor holds
    # it only ~1.2s, so 4s is plenty and it bills at the cheaper no-audio rate.
    secs = make_video_clip(img, detail_insert_prompt(detail, setting), DETAIL_DUR,
                         clip_path, generate_audio=False)
    if secs == 0.0 or not os.path.exists(clip_path):
        return None, images_paid, 0.0
    return beat, images_paid, secs


def detail_insert_prompt(detail: str, setting: str) -> str:
    """Seedance prompt for the silent detail beat: a slow, quiet push-in on the object, no
    people, tiny real-world motion so it reads as footage rather than a frozen still."""
    obj = (detail or "the object").strip().rstrip(".")
    place = (setting or "the room").strip().rstrip(".")
    return (f"A slow, quiet cinematic push-in on {obj} in {place}. NObody in frame — no "
            f"person, no hands, no face — only the object. Tiny ambient motion (a slight "
            f"drift of light, dust, or the object settling), the scene already in gentle "
            f"movement from the first frame. {STYLE}. No text.")


# --- Seedance clip + re-voice ---------------------------------------------------

def clip_duration(text: str) -> str:
    """Pick a clip length for one spoken line. Seedance accepts 5-10s, so we use 5/6/8
    (5s is the shortest safe value — a shorter request can be rejected)."""
    n = len((text or "").split())
    secs = n / 2.5 + 1.5           # ~2.5 words/sec + a little air
    return "5s" if secs <= 5 else "6s" if secs <= 6 else "8s"


def _line_windows(lines: list):
    """Give each dialogue line its own time slice (~2.5 words/sec, min 1.8s so even a
    short line has room to land) plus the running total, clamped to Seedance's 8s ceiling.
    These slices become an explicit timeline in the prompt. Without a per-speaker time
    budget Seedance tries to voice both lines at once, rushes the hand-off, and fills the
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


def scene_duration(lines: list) -> str:
    """Clip length for a whole dialogue scene: match the summed line windows so the
    clip is only as long as the actual speech. Leaving an empty tail is what lets the
    model invent extra mumbling to fill it, so we size tight. Clamped to 5/6/8s."""
    _, total = _line_windows(lines)
    return "5s" if total <= 5.5 else "6s" if total <= 6.5 else "8s"


def _short_err(e) -> str:
    """One short line from a fal error (never the whole echoed request/prompt)."""
    s = str(e)
    low = s.lower()
    if "content_policy" in low or "content checker" in low or "flagged" in low:
        return "blocked by Seedance's content filter (wording too explicit)"
    return s[:140]


# A Seedance generation is the one call we CANNOT afford to lose: it is billed the moment
# fal starts it, and the result only reaches us if we are still listening. fal_client's
# subscribe() polls the queue in a loop with no overall deadline, so a stalled poll
# leaves it waiting forever (it hung a real run for 23 minutes on a dead connection).
# We therefore submit the job ourselves and poll with our own deadline. Crucially we
# record the request_id on disk BEFORE waiting: a job we already paid for can then be
# collected on the next run instead of being submitted — and paid for — a second time.
VIDEO_MAX_WAIT = 600      # give one clip this long to finish before we stop waiting
VIDEO_POLL_EVERY = 5      # ask the queue for its status this often
VIDEO_JOBS = os.path.join(OUT_DIR, "_video_jobs.json")   # out_file -> fal request_id
# The job store is one JSON file that every render reads-modifies-writes. With clips now
# rendering in parallel (MAX_PARALLEL_RENDERS), several threads touch this file at once, so we
# guard it with a lock — otherwise two threads' writes race and one job id is lost (and a job we
# paid for becomes uncollectable). RLock (reentrant) so a locked helper can call another locked
# helper on the same thread without deadlocking.
_JOBS_LOCK = threading.RLock()


def _video_jobs() -> dict:
    """The saved out_file -> fal request_id map of clips we have paid to start, so a run
    killed mid-wait can collect the finished video next time instead of paying twice."""
    with _JOBS_LOCK:
        try:
            with open(VIDEO_JOBS) as f:
                return json.load(f)
        except Exception:
            return {}


def _remember_video_job(out_path: str, job_id: str, model: str):
    """Record a submitted job (id + which model made it) so a later run can collect it for
    free. The model is stored too because the collect call must rebuild the handle against
    the SAME endpoint — Seedance, Sync 2.0 and LatentSync all use this one store."""
    with _JOBS_LOCK:                      # hold the lock across the read AND the write
        jobs = _video_jobs()
        jobs[os.path.basename(out_path)] = {"id": job_id, "model": model}
        with open(VIDEO_JOBS, "w") as f:
            json.dump(jobs, f, indent=2)


def _forget_video_job(out_path: str):
    """Drop a job once its video is safely on disk (or is permanently dead)."""
    with _JOBS_LOCK:                      # hold the lock across the read AND the write
        jobs = _video_jobs()
        if jobs.pop(os.path.basename(out_path), None) is not None:
            with open(VIDEO_JOBS, "w") as f:
                json.dump(jobs, f, indent=2)


def _save_video(result: dict, out_path: str) -> bool:
    """Download the finished video out of a fal result payload. Seedance returns
    {"video": {"url": ...}}; we also accept a {"videos": [...]} list defensively."""
    video = result.get("video")
    if not video and result.get("videos"):
        video = result["videos"][0]
    url = video.get("url") if isinstance(video, dict) else video
    if not url:
        return False
    with open(out_path, "wb") as f:
        f.write(requests.get(url, timeout=180).content)
    return os.path.exists(out_path)


def _await_video(handle, out_path: str) -> bool:
    """Wait for one submitted job, polling with OUR deadline so a stalled queue poll can
    never hang the run. A failed poll is ignored and retried — the job keeps running on
    fal's side regardless of whether we are listening. Returns True if the video landed
    on disk."""
    deadline = time.time() + VIDEO_MAX_WAIT
    while time.time() < deadline:
        try:
            if isinstance(handle.status(), fal_client.Completed):
                return _save_video(handle.get(), out_path)
        except Exception:
            pass          # transient poll failure — the job is unaffected, just retry
        time.sleep(VIDEO_POLL_EVERY)
    return False


def _mute_clip(path: str) -> bool:
    """Replace a clip's audio track with silence, in place. Used for SILENT beats (the
    face-free detail inserts): we render them with generate_audio=False, but we still give
    them a silent stereo track so every beat has a matching audio stream for the editor's
    concat (a clip with NO audio stream at all breaks the concat). It also strips any stray
    ambience the model might bake in, so a detail insert is guaranteed truly silent."""
    tmp = path + ".mute.mp4"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", path,
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                        "-shortest", tmp], check=True, capture_output=True)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"    (could not mute detail insert: {e})")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def _submit_and_collect(model: str, arguments: dict, out_path: str, label: str) -> bool:
    """Run ONE fal video-producing job crash-safely — used for the Seedance render AND for the
    lip-sync re-dubs, since they are all fal queue jobs billed the moment they start. First it
    collects anything a previous run already paid for (a job submitted, then the run died
    waiting, keeps running on fal); otherwise it submits, saves the request_id + model BEFORE
    waiting (so a kill mid-wait can collect it next time instead of paying twice), and polls
    with our own deadline. Returns True if a video landed on disk."""
    prior = _video_jobs().get(os.path.basename(out_path))
    if prior:
        pid = prior["id"] if isinstance(prior, dict) else prior
        pmodel = prior.get("model", model) if isinstance(prior, dict) else model
        try:
            handle = fal_client.SyncRequestHandle.from_request_id(
                fal_client.sync_client._client, pmodel, pid)
            done = isinstance(handle.status(), fal_client.Completed)
            if (done and _save_video(handle.get(), out_path)) or \
               (not done and _await_video(handle, out_path)):
                _forget_video_job(out_path)
                print(f"    (collected a {label} a previous run already paid for, $0)")
                return True
        except Exception as e:
            print(f"    could not collect the earlier {label} job ({_short_err(e)}); re-submitting")

    for attempt in range(1, 4):
        try:
            handle = fal_client.submit(model, arguments=arguments)
            # Save the id BEFORE waiting: from here on the job is billable, so it must be
            # recoverable even if this process is killed mid-wait.
            _remember_video_job(out_path, handle.request_id, model)
            print(f"    {label} rendering (job {handle.request_id[:8]}, "
                  f"up to {VIDEO_MAX_WAIT // 60} min) ...")
            if _await_video(handle, out_path):
                _forget_video_job(out_path)
                return True
            print(f"    {label} attempt {attempt} timed out after {VIDEO_MAX_WAIT // 60} min; "
                  "job saved, re-run to collect it without paying again")
            return False
        except Exception as e:
            msg = _short_err(e)
            print(f"    {label} attempt {attempt} failed: {msg}")
            if "content filter" in msg:          # same wording will fail again — give up
                return False
            time.sleep(5)
    return False


def make_video_clip(image_path: str, prompt: str, duration: str, out_path: str,
                    generate_audio: bool = True) -> float:
    """Render one Seedance 1.5 pro clip from the start image. The voiceover pipeline always
    calls this with generate_audio=False (SILENT — half price, and we never hear the
    characters); the clip then gets a silent stereo track and the narrator's voiceover is
    laid over it later. (generate_audio=True is legacy, for the old lip-sync path.) Returns
    the billed seconds, or 0.0 (no file) on failure so one bad beat never crashes the run."""
    secs = float(int(duration[:-1]))
    # Seedance takes the duration as a bare number ("5", not "5s") and IGNORES the image
    # aspect unless aspect_ratio is sent. Ordering is deliberate: the cost logger truncates
    # the request body, so the SHORT params (especially generate_audio, which sets the billed
    # rate) go BEFORE the long prompt — otherwise reconcile.py can't tell if audio was on.
    args = {"generate_audio": generate_audio, "duration": duration[:-1],
            "resolution": VIDEO_RES, "aspect_ratio": VIDEO_ASPECT,
            "image_url": fal_client.upload_file(image_path), "prompt": prompt}
    if _submit_and_collect(VIDEO_MODEL, args, out_path, f"seedance {duration}"):
        if not generate_audio:
            _mute_clip(out_path)
        return secs
    return 0.0


def lipsync(video_path: str, audio_path: str, out_path: str, two_faces: bool) -> bool:
    """Re-dub a talking clip's MOUTHS onto our correct-voice audio, keeping the same faces.
    DIALOGUE (two_faces=True) uses Sync 2.0, whose active-speaker detection maps each voice to
    the right of two faces; NARRATION (one face) uses the cheaper LatentSync. cut_off keeps
    the video only as long as the voice (we always render the clip a bit LONGER than the
    audio, so no word is ever cut). Returns True on success; on failure the caller keeps the
    original clip so a scene is never lost."""
    model = SYNC_MODEL if two_faces else LATENTSYNC_MODEL
    args = {"video_url": fal_client.upload_file(video_path),
            "audio_url": fal_client.upload_file(audio_path)}
    if model == SYNC_MODEL:
        args.update({"model": "lipsync-2", "sync_mode": "cut_off"})
    return _submit_and_collect(model, args, out_path, "sync-2 dub" if two_faces else "latentsync dub")


# --- Correct-voice audio: ElevenLabs TTS + line assembly ------------------
# We build the RIGHT words ourselves (Seedance mis-reads them), one line at a time in each
# character's own locked voice, then re-dub the clip onto this audio. Making the audio first
# also lets us size the Seedance clip to FIT it, so no word is ever cut on the re-dub.

def tts(voice_id: str, text: str, out_path: str):
    """ElevenLabs text-to-speech in one fixed voice. Returns (seconds, char_count) for cost,
    or (0.0, 0) on failure (writing no file). Correct words by construction — the model reads
    exactly the text we send."""
    text = (text or "").strip()
    if not voice_id or not text:
        return 0.0, 0
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
            headers={"xi-api-key": ELEVEN_KEY},
            json={"text": text, "model_id": TTS_MODEL, "voice_settings": VOICE_SETTINGS},
            timeout=120)
        if r.status_code != 200:
            print(f"    tts failed {r.status_code}: {r.text[:120]}")
            return 0.0, 0
        with open(out_path, "wb") as f:
            f.write(r.content)
        return audio_duration(out_path), len(text)
    except Exception as e:
        print(f"    tts error ({e})")
        return 0.0, 0


def _silence(seconds: float, out_path: str):
    """A short silent mp3 (same format as the TTS) used as a lead-in / between-turn gap."""
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{seconds}", "-b:a", "128k", out_path],
                   check=True, capture_output=True)


def build_line_audio(lines: list, chars_by_name: dict, out_path: str):
    """TTS every dialogue line in its speaker's OWN voice, then join them into ONE track in
    script order: a short lead-in, each line, and a gap between turns. Keeping the lines in
    order and turn-separated is exactly what lets Sync 2.0 map each voice to the right face.
    Returns (total_seconds, total_chars), or (0.0, 0) if nothing could be voiced."""
    segs, chars, voiced = [], 0, 0
    lead = out_path + ".s.mp3"
    _silence(0.4, lead)
    segs.append(lead)
    for k, ln in enumerate(lines or []):
        c = chars_by_name.get(ln.get("character", ""), {})
        seg = out_path + f".{k}.mp3"
        secs, ch = tts(c.get("voice_id", ""), ln.get("line", ""), seg)
        if secs <= 0:
            continue
        if voiced:                                 # a beat of silence between consecutive turns
            gap = out_path + f".g{k}.mp3"
            _silence(0.5, gap)
            segs.append(gap)
        segs.append(seg)
        chars += ch
        voiced += 1
    if not voiced:
        for s in segs:
            if os.path.exists(s):
                os.remove(s)
        return 0.0, 0
    ins = []
    for s in segs:
        ins += ["-i", s]
    n = len(segs)
    subprocess.run(["ffmpeg", "-y", *ins, "-filter_complex",
                    "".join(f"[{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1", out_path],
                   check=True, capture_output=True)
    for s in segs:
        if os.path.exists(s):
            os.remove(s)
    return audio_duration(out_path), chars


def fit_duration(audio_secs: float) -> str:
    """Seedance clip length that COMFORTABLY covers the voice track (so the re-dub never cuts
    a word). Seedance allows 5-10s; we take the audio length + a little air, rounded up and
    clamped."""
    n = int(math.ceil(audio_secs + 0.8))
    return f"{max(5, min(10, n))}s"


def billed_seconds(sc: dict) -> float:
    """The Seedance seconds a scene's clip was billed. Prefer the exact value we stored when
    we rendered it (sc["clip_seconds"]); if it's missing (an older run, or a clip made before
    we stored it), re-derive the request length the clip was sized to — the voiceover length
    via fit_duration, exactly how make it below — so a reused clip still contributes its real
    price to the video total."""
    if sc.get("clip_seconds"):
        return float(sc["clip_seconds"])
    vo = sc.get("vo_seconds", 0.0) or 0.0
    dur = fit_duration(vo) if vo > 0 else scene_duration(sc.get("dialogue", []))
    return float(int(dur[:-1]))


def _mux_audio(video_path: str, audio_path: str, out_path: str) -> bool:
    """Fallback only: if a lip-sync re-dub fails, at least put our CORRECT-WORDS audio on the
    Seedance video (trimmed to the voice length) so the scene keeps the right words — the
    mouths just won't match. Better than losing the scene."""
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                        "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                        "-shortest", out_path], check=True, capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f"    (mux fallback failed: {e}); keeping the raw clip")
        shutil.copyfile(video_path, out_path)
        return os.path.exists(out_path)


def _render_task(task: dict) -> dict:
    """Render ONE queued scene: its silent Seedance clip, then lay the pre-made voiceover over
    it. This runs in a WORKER THREAD (many at once), so it must touch only this scene's own
    files — it does: a unique temp `_src_NN.mp4` and this scene's `clip_NN.mp4`. The only shared
    thing it hits is the fal-job store, which is lock-guarded. It does NOT mutate any counters or
    the analysis data — it just returns the billed seconds and whether it worked, so the main
    thread can total the cost and set the scene's beats in order, with no cross-thread races."""
    i = task["i"]
    src = os.path.join(OUT_DIR, f"_src_{i:02d}.mp4")
    # Wrap the whole thing: a worker that raises would otherwise re-raise in the main thread and
    # crash the entire render pass. Catching it here means one bad clip just skips (ok=False),
    # exactly like the old serial loop — the other clips are unaffected.
    try:
        secs = make_video_clip(task["start_img"], task["prompt"], task["dur"], src,
                               generate_audio=False)
        if secs == 0.0 or not os.path.exists(src):
            return {"task": task, "secs": 0.0, "ok": False}
        # Lay the voiceover over the silent clip (NO lip-sync). -shortest trims the picture to the
        # voice, so the finished clip is exactly as long as the narration; no voiceover -> keep
        # the silent clip as-is. The voiceover file is a kept output; only the temp _src is removed.
        if task["vo_secs"] > 0:
            _mux_audio(src, task["vo_path"], task["clip_path"])
        else:
            os.replace(src, task["clip_path"])
        if os.path.exists(src):
            os.remove(src)
        return {"task": task, "secs": secs, "ok": True}
    except Exception as e:
        print(f"    [{i}] render failed ({_short_err(e)})")
        return {"task": task, "secs": 0.0, "ok": False}


# --- Ambient bed: continuous room tone per location ------------------------
# The EDITOR (assemble.py) lays a seamlessly-looping ambient bed UNDER the whole
# scene so the audio never drops to silence at a cut — the single biggest trick
# for hiding the seams between separately-generated clips. We generate ONE short
# loop per unique location here (ElevenLabs Sound-Effects) and record it on each
# scene; the editor loops it to length and mixes it low under the dialogue.

def ambient_prompt(desc: str) -> str:
    """A short prompt for the CONTINUOUS background sound of a place, built from the
    scene's `ambience` description (e.g. 'low party chatter and clinking glasses', 'train
    rumble on the rails'). Indistinct and loopable — no clear speech, no foreground music,
    no one-off effects — so it sits UNDER the narration for the whole scene."""
    s = (desc or "a quiet room").strip().rstrip(".")
    return (f"Continuous background ambience: {s}. A steady, seamless bed that loops with "
            f"no gaps, atmospheric and indistinct — no clear or intelligible speech, no "
            f"foreground music, no sudden one-off effects.")


def make_ambient(desc: str, out_path: str, seconds: int = 15) -> float:
    """Generate ONE seamlessly-looping ambient bed for a location with the
    ElevenLabs Sound-Effects API (loop=true so it repeats with no click). Returns
    the generated seconds (for cost), or 0.0 on failure — in which case the scene
    simply has no bed and the editor falls back to the clips' own audio."""
    try:
        r = requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": ambient_prompt(desc),
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


def make_music(out_path: str, seconds: int = 22) -> float:
    """Generate ONE looping underscore for the whole video (ElevenLabs Sound-Effects,
    loop=true). The editor loops and DUCKS this under the dialogue so it fills the gaps
    between lines and glues the cuts without ever masking a voice. We ask for a sparse,
    tense bed with NO strong melody on purpose — a hummable tune would fight the drama
    and date fast. Returns the generated seconds (for cost), 0.0 on failure (the editor
    then simply lays no music)."""
    prompt = ("Sparse, tense cinematic underscore for a prestige legal drama: low sustained "
              "strings and a slow soft pulse, dark and restrained, no strong melody, no drums, "
              "no beat drop — a seamless quiet loop that sits under dialogue.")
    try:
        r = requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": prompt, "duration_seconds": seconds, "loop": True},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"    music bed failed {r.status_code}: {r.text[:120]}")
            return 0.0
        with open(out_path, "wb") as f:
            f.write(r.content)
        return float(seconds)
    except Exception as e:
        print(f"    music bed error ({e})")
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


# --- Coverage + reactions (both disabled — see the flags above) -------------

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
    This is what guarantees Seedance animates the CORRECT person in the CORRECT place —
    with two faces in a two-shot Seedance sometimes lip-syncs the wrong one; here there
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


def group_shot(names: list, chars_by_name: dict, base_shot: str) -> str:
    """Build the wide camera line with FORCED left-to-right positions for however many
    people are in the shot, so the model can't shuffle them into a random order (which
    would make each scene's geography jump). Two people face each other across a table;
    three or four are staged across the frame so every face stays readable."""
    n = len(names)
    if n == 1:
        return base_shot
    if n == 2:
        # OVER-THE-SHOULDER framing baked into the STILL (not a mid-clip camera move): the
        # SECOND person sits in the foreground with their back/shoulder to the lens, the
        # FIRST faces the camera over that shoulder, in focus. Because the foreground body
        # is REAL in the composed frame and the video camera then stays locked on it, Seedance
        # never has to invent a shoulder mid-shot — which is what used to clone a person.
        near = descriptor(chars_by_name.get(names[1], {}))   # foreground, back to camera
        far = descriptor(chars_by_name.get(names[0], {}))    # faces camera, in focus
        parts = [f"cinematic over-the-shoulder shot across a table: {near} sits in the "
                 f"FOREGROUND closest to the camera, seen from BEHIND — only the back of "
                 f"their head and one shoulder, softly out of focus; {far} sits across from "
                 f"them and faces toward the camera over that shoulder, in sharp focus with "
                 f"their whole face clearly visible"]
    else:
        # Name a slot per person, left to right, so nobody is hidden behind anyone else.
        slots = ["on the far LEFT", "LEFT of centre", "RIGHT of centre", "on the far RIGHT"]
        placed = ", ".join(
            f"{descriptor(chars_by_name.get(nm, {}))} {slots[k] if k < len(slots) else 'beside them'}"
            for k, nm in enumerate(names))
        parts = [f"medium wide {n}-shot, {placed}, spread across the frame so every "
                 "face is clearly visible, turned toward whoever is speaking"]
    if base_shot:
        parts.append(base_shot)
    return ". ".join(parts)


def add_silent_audio(video_path: str, out_path: str) -> bool:
    """Give a video-only clip (a Seedance reaction rendered with audio OFF) a silent
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
    """Seedance prompt for a SILENT reaction beat: the listener reacts with their face
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
    """Seedance prompt for a line rendered from a CROPPED SINGLE (only the speaker is in
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
    """Seedance prompt for ONE dialogue line: the speaker acts + says it; the others
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
    """'She replies' / 'He replies'. The word 'replies' signals to Seedance that the next
    line is a consecutive response, not simultaneous speech — which is what keeps the
    two speakers from overlapping into one garbled voice."""
    return "She replies" if character_gender(c) == "female" else "He replies"


def build_scene_prompt(sc: dict, chars_by_name: dict) -> str:
    """Prompt for a WHOLE dialogue scene as ONE continuous Seedance clip. The clip is rendered
    SILENT and the narrator's voiceover is laid over it — the characters are never heard — so
    the model's spoken WORDS do not matter at all. What we need is believable acting: the cast
    naturally mouthing an exchange in the right TURN ORDER (A, then B, ...) so the scene looks
    alive under the voiceover. The locked camera, identity locks and anti-clone rules that hold
    the faces are kept."""
    lines = sc.get("dialogue", [])
    speakers = sc.get("characters", [])
    # Everyone in the frame, not just the talkers: the silent people are composed into
    # the start image, so the prompt must account for them or Seedance drops or redraws them.
    onscreen = [n for n in (sc.get("onscreen") or speakers)] or speakers
    who = {n: descriptor(chars_by_name.get(n, {})) for n in onscreen}
    n_on = len(onscreen)
    hold_shot = ("a steady over-the-shoulder shot — one person's shoulder held in the "
                 "foreground, the other facing the camera in sharp focus" if n_on <= 2 else
                 f"a steady medium wide {n_on}-shot holding everyone in the room")
    # Turn order as NATURAL directions (no clock times), so the model paces itself and never
    # freezes to hit a window. The line text rides along only as an emotion/length hint — it
    # is replaced on the re-dub, so exact wording is irrelevant.
    turns = []
    for j, d in enumerate(lines):
        spk = who.get(d.get("character", ""), "the other person").capitalize()
        tone = f" ({d['emotion'].strip()})" if d.get("emotion") else ""
        when = "speaks first" if j == 0 else "then replies"
        turns.append(f"{spk} {when}{tone}, talking naturally and continuously through their turn")
    turn_line = ("; ".join(turns) + "." if turns else
                 "the people talk naturally back and forth.")
    # CAST block: pin EVERY on-screen person to their locked identity (hair, build,
    # CLOTHING COLOUR) so a face can't be redrawn or an outfit recoloured mid-shot.
    cast = " ".join(f"{who.get(n, 'a person').capitalize()} = "
                    f"{identity_lock(chars_by_name.get(n, {}))}." for n in onscreen)
    # Name the people who never speak, so Seedance keeps them present and reacting instead
    # of treating them as scenery to drift or quietly drop out of frame.
    silent = [who[n] for n in onscreen if n not in speakers]
    silent_line = ""
    if silent:
        verb = "stays" if len(silent) == 1 else "stay"
        silent_line = (f" {', '.join(silent).capitalize()} {verb} in the room throughout, "
                       "silent — mouth closed, listening and reacting with the face.")
    # The scene's blocking, so the shot has a concrete physical action, not just heads.
    action = (sc.get("action") or "").strip().rstrip(".")
    action_line = f" {action}." if action else ""
    if n_on <= 2:
        staging = ("two people mid-conversation in an over-the-shoulder framing — the person "
                   "facing the camera looks at the foreground person beside the lens, never "
                   "straight into the lens")
    else:
        staging = (f"{n_on} people in one conversation, turned toward whoever "
                   "is speaking, never the camera")
    return (
        # Lead with motion so Seedance doesn't open on a frozen staring frame.
        "ONE continuous LOCKED shot, no cuts / intimate cinematic legal drama.\n"
        f"A continuous cinematic scene, already in motion from the first frame — {staging}. "
        # ONE fixed camera for the whole clip; the reframe/reverse angle is banned because
        # building it makes Seedance duplicate a person into the foreground.
        f"CAMERA: {hold_shot}, held as ONE fixed angle for the entire clip with at most a "
        f"very slight, slow push-in. The camera does NOT cut, swing, orbit, or change to a "
        f"different angle. The foreground person keeps their back to the camera throughout; "
        f"do NOT turn them around, and do NOT add, duplicate or reveal any second copy of "
        f"anyone. {STYLE}, natural room ambience.\n"
        f"CAST: {cast}\n"
        f"CONVERSATION: the people have a natural, flowing back-and-forth. {turn_line} "
        "Each speaker's mouth moves SMOOTHLY and CONTINUOUSLY through their whole turn — no "
        "long pause, no freezing, no stopping mid-sentence — then their mouth closes and they "
        "listen while the other talks. Only ONE person speaks at a time, in the order above."
        f"{silent_line}{action_line} "
        # Pin the blocking to what the start frame shows — the model likes to invent big moves
        # (a seated person "stands" out of a chair that was never rendered).
        "Everyone keeps the position the opening frame puts them in — whoever is seated stays "
        "seated, whoever is standing stays standing; nobody stands up, sits down or walks off, "
        "and no furniture or object appears, vanishes or changes place. Nuanced facial "
        "micro-expressions, every person's face and clothing colour identical throughout. "
        # Hold one stable body per person (the model can briefly clone someone mid-clip).
        "Exactly one of each person on screen at all times — NEVER duplicate, clone, split, "
        "mirror or ghost a person; keep one stable body for each person the whole time."
    )


def narration_prompt(sc: dict, lead_c: dict) -> str:
    """Seedance prompt for a memoir narration beat: the lone protagonist performs it
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
        f"first person, like a memoir confession: \"{sc.get('narration', '')}\". Their mouth "
        f"moves smoothly and CONTINUOUSLY while speaking — no freezing, no stopping "
        f"mid-sentence. Nobody else is present. {STYLE}."
    )


def build_narration_vo_prompt(sc: dict, lead_c: dict) -> str:
    """Silent-motion prompt for a NARRATION beat in the voiceover style: the lone
    protagonist is present in a fitting place and moves naturally, but does NOT speak on
    camera — mouth closed, lost in thought — because we hear their voiceover instead. No
    lip movement means nothing to lip-sync and nothing to look wrong under the VO."""
    who = descriptor(lead_c).capitalize() if lead_c else "The narrator"
    lock = identity_lock(lead_c) if lead_c else ""
    lock_line = f" ({lock})" if lock else ""
    return (
        "The scene is already in motion from the very first frame. ONE person is ALONE in "
        f"the shot, nobody else present. {sc.get('shot', 'slow push-in on a lone figure')}. "
        f"{sc.get('action', '')}. {who}{lock_line} is lost in thought — contemplative, "
        "MOUTH CLOSED, NOT speaking, NOT talking to the camera. They breathe and move "
        "naturally and may glance toward the lens, but they say nothing: no lip movement, no "
        f"speech. {STYLE}."
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
    # The protagonist, used only as the narration fallback lead (main_names[0]).
    main_names = [c["fictional_name"] for c in named[:1] if c.get("file")]

    # ONE voice for the whole video: the PROTAGONIST's first-person voiceover (memoir style).
    # Same pick as scene_writer.lead_name — the first non-anonymous character. Every scene,
    # dialogue or narration, is narrated by this one lead, so there is exactly one voice and
    # never any lip-sync to get wrong.
    lead_name = next((c["fictional_name"] for c in data.get("characters", [])
                      if not c.get("anonymous")), None) or (main_names[0] if main_names else None)
    lead_c = chars_by_name.get(lead_name, {})

    # All audio (voiceover, ambience, music) was made in the previous step (audio_maker) and
    # is recorded on each scene: sc["vo_file"]/["vo_seconds"] for the voiceover, sc["ambient"]
    # for the room bed. This step only renders the SILENT videos and lays that voiceover over
    # them, so the only paid work here is Seedance (video) + Nano Banana 2 (scene images).
    # Two tallies per paid thing so the final table can separate the video's TRUE price from
    # what THIS run paid: the "spent" ones count only pieces we generate now; the "reused" ones
    # count pieces collected from a previous run's disk (they cost $0 now but their real price
    # still belongs in the video total, so a repair/redo total matches a fresh build).
    scene_video_seconds = 0.0   # Seedance SILENT scene clips rendered THIS run ($0.026/s)
    detail_video_seconds = 0.0  # Seedance seconds for silent detail inserts made THIS run
    scene_images = 0            # composed images we PAID for this run (scene composites + details)
    reused_video_seconds = 0.0  # Seedance seconds of clips reused from a prior run (paid before)
    reused_images = 0           # composed images reused from a prior run (paid before)
    reused_pro = 0              # of those reused images, how many were Pro-fallback composes
    # The loop below only COMPOSES each scene image (serial, so the room anchor stays consistent)
    # and queues a render task per scene that needs a new clip; the queued clips are then rendered
    # all at once, in parallel, after the loop (see MAX_PARALLEL_RENDERS).
    render_tasks = []
    location_ref = {}       # setting -> first image made there (keep the room identical)
    compose_cache = {}      # (same people, same place) -> reuse that image (no re-pay)
    coverage_cache = {}     # (same people, same place) -> reuse the angle set (no re-pay)
    detail_by_loc = {}      # setting -> detail insert beat made there (reuse, no re-pay)

    # Header that tells the truth about this run: how many scene clips actually need rendering
    # (their clip file is missing) vs how many are already on disk and will just be reused. On a
    # redo/repair only the deleted clips are missing, so this says "rendering 1, reusing 8"
    # instead of a misleading "Rendering 9 scenes".
    to_render = [i for i, _ in enumerate(scenes, 1)
                 if not os.path.exists(os.path.join(OUT_DIR, f"clip_{i:02d}.mp4"))]
    n_render, n_reuse = len(to_render), len(scenes) - len(to_render)
    if n_render == 0:
        print(f"All {len(scenes)} scene clips are already on disk — reusing them, nothing to render.")
    elif n_reuse == 0:
        print(f"Rendering {len(scenes)} scenes ...")
    else:
        nums = ", ".join(str(i) for i in to_render)
        print(f"{len(scenes)} scenes: rendering {n_render} "
              f"(scene{'s' if n_render != 1 else ''} {nums}), "
              f"reusing {n_reuse} already on disk from the last run ...")

    for i, sc in enumerate(scenes, 1):
        clip_name = f"clip_{i:02d}.mp4"
        clip_path = os.path.join(OUT_DIR, clip_name)
        sc_setting = sc.get("setting", setting) or setting

        # Resume guard FIRST (normal pipeline, detail inserts off): if this scene's clip is
        # already on disk we reuse the whole scene untouched — no image compose, no render, ONE
        # clean line. It costs $0 now, but its clip seconds and its own composed image still
        # count toward the video's TRUE total below. Doing this BEFORE the compose work is what
        # removes the old confusing double "reusing scene image" + "reused clip" pair for a scene
        # we aren't touching. (When detail inserts are on, we fall through to the fuller path so
        # the scene's insert beat is rebuilt too.)
        if os.path.exists(clip_path) and not DETAIL_INSERTS:
            is_narr = sc.get("type") == "narration"
            who = ((sc.get("characters") or [lead_name or "?"])[0] if is_narr
                   else " + ".join(sc.get("characters", [])) or "?")
            sc["beats"] = [{"file": clip_name, "kind": sc.get("type", "dialogue"),
                            "speaker": lead_name if is_narr else None, "silent": False}]
            reused_video_seconds += billed_seconds(sc)
            # Count the scene's OWN composed image only if it has its own file. Scenes that
            # shared another scene's image (same cast + same place) have no file of their own,
            # so counting them would double-count one paid image.
            own_img = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
            if os.path.exists(own_img):
                reused_images += 1
                reused_pro += sc.get("image_pro", 0)
                # Keep the room anchor so a scene RE-rendered this run in the same location still
                # matches the room of its untouched neighbours (without it the room can drift).
                location_ref.setdefault(sc_setting.strip().lower(), own_img)
            print(f"  [{i}] {sc.get('type','scene').upper()} [{who}] -> {clip_name} "
                  f"(reused from last run, $0)")
            continue

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
            # EVERYONE the scene puts in the room goes in the frame — the speakers
            # plus any silent people present. We compose from "onscreen" (not just the
            # speakers) so every visible face comes from a locked portrait; a person
            # named in the script but missing from the composite is exactly what makes
            # the model invent a random face. Anyone without a portrait is dropped for
            # the same reason. Falls back to the speakers for older analysis files.
            names = [n for n in (sc.get("onscreen") or sc.get("characters", []))
                     if chars_by_name.get(n, {}).get("file")]
            if not names:
                print(f"  [{i}] DIALOGUE skipped: no character images.")
                continue
        # Use a single cropped FRONT view per character as the compose reference, NOT the
        # whole 4x2 sheet — see sheet_ref: the grid made the compositor stack the same
        # person twice on solo (narration) shots.
        portraits = [sheet_ref(os.path.join(OUT_DIR, chars_by_name[n]["file"]), n)
                     for n in names]

        loc_key = sc_setting.strip().lower()

        # Compose the scene image(s). We do NOT reuse a previous clip's last frame —
        # that frame is mid-talk, which makes Seedance continue the wrong speaker. If the
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

        # Did THIS scene's image need the Pro fallback? (captured around the compose call
        # below and shown on the scene's summary line, so each step's log flags it clearly.)
        used_pro_fallback = False
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
                    # Resume guard: this scene image was composed in a previous run — reuse it,
                    # don't re-pay Nano Banana. It cost $0 now, but it's still one image the
                    # video is built from, so it counts toward the TRUE total (not toward spent).
                    print(f"    (reusing scene image {os.path.basename(start_img)} "
                          "— already on disk, $0)")
                    reused_images += 1
                    reused_pro += sc.get("image_pro", 0)
                    location_ref.setdefault(loc_key, start_img)
                    compose_cache[cache_key] = start_img
                else:
                    # For DIALOGUE, seat the two people as a clean facing two-shot;
                    # narration is one person. The identity locks (in the same order as
                    # the portraits) name each reference and pin each person's clothing
                    # colour so the composite can't blend or recolour them.
                    compose_shot = (group_shot(names, chars_by_name, sc.get("shot", ""))
                                    if sc.get("type") == "dialogue" and len(names) >= 2
                                    else sc.get("shot", ""))
                    locks = [identity_lock(chars_by_name[n]) for n in names]
                    pf_before = _pro_fallbacks       # detect if this compose falls back to Pro
                    if compose_scene_image(portraits, sc_setting, compose_shot,
                                           sc.get("action", ""), start_img,
                                           room_ref=room_ref, people=locks):
                        used_pro_fallback = _pro_fallbacks > pf_before
                        scene_images += 1
                        # Remember on the scene whether it fell back to Pro, so if a later run
                        # reuses this image it still prices it as Pro (not the cheaper NB2 rate).
                        sc["image_pro"] = 1 if used_pro_fallback else 0
                        location_ref.setdefault(loc_key, start_img)
                        compose_cache[cache_key] = start_img
        if not (start_img and os.path.exists(start_img)):
            print(f"  [{i}] skipped: no scene image could be composed.")
            continue

        # --- VO SCENE: SILENT cinematic clip + the LEAD's first-person voiceover over it ---
        # ONE voice carries the whole video: the protagonist narrating. We render the scene
        # as SILENT motion (characters act / mouth their lines, muted — no lip-sync anywhere,
        # so nothing can put the wrong voice on the wrong mouth), then lay the lead's
        # voiceover on top. Narration and dialogue scenes share this single path.
        is_narr = sc.get("type") == "narration"
        speakers = sc.get("characters", [])
        who = names[0] if is_narr else " + ".join(speakers)

        # Detail insert (dialogue scenes only): open a NEW location on a silent, face-free
        # object shot so the viewer is oriented before the scene starts. Once per location.
        insert_beats = []
        if not is_narr and loc_key not in detail_by_loc:
            detail_by_loc[loc_key] = None
            ins, imgs, dsecs = make_detail_insert(
                i, sc.get("detail", ""), sc_setting, room_ref or start_img)
            scene_images += imgs
            detail_video_seconds += dsecs
            if ins:
                detail_by_loc[loc_key] = ins
                insert_beats = [ins]

        beat = {"file": clip_name, "kind": sc.get("type", "dialogue"),
                "speaker": lead_name if is_narr else None, "silent": False}

        # Resume guard for the DETAIL-INSERTS-ON path only (the normal detail-off run already
        # reused this clip at the top of the loop). A finished clip on disk is reused for free,
        # but its billed seconds still belong in the video's TRUE total; the reused image was
        # already counted in the compose section above, so we only add the clip here.
        if os.path.exists(clip_path):
            sc["beats"] = insert_beats + [beat]
            reused_video_seconds += billed_seconds(sc)
            print(f"  [{i}] {sc.get('type','scene').upper()} [{who}] -> {clip_name} (reused, $0)")
            continue

        # Silent-motion prompt: narration = the lead alone, contemplative (mouth closed);
        # dialogue = the on-screen cast act and silently mouth their lines.
        prompt = (build_narration_vo_prompt(sc, lead_c) if is_narr
                  else build_scene_prompt(sc, chars_by_name))

        # The voiceover for THIS scene was already made by audio_maker (step 6); read its file
        # and length so the video can be sized to it and the voice laid over it. DRAFT mode is a
        # cheap preview: the shortest clip and no voiceover.
        if DRAFT:
            vo_path, vo_secs, dur = "", 0.0, "5s"
        else:
            vo_file = sc.get("vo_file", "")
            vo_path = os.path.join(OUT_DIR, vo_file) if vo_file else ""
            vo_secs = sc.get("vo_seconds", 0.0) if vo_path and os.path.exists(vo_path) else 0.0
            # Size the clip to cover the voiceover (a touch longer so the VO is never clipped).
            dur = fit_duration(vo_secs) if vo_secs > 0 else scene_duration(sc.get("dialogue", []))

        # Don't render inline — QUEUE the render. All queued clips are fired together, in
        # parallel, after this loop. Everything the render + its result line needs is captured
        # now, while we have this scene's context.
        render_tasks.append({
            "i": i, "sc": sc, "clip_name": clip_name, "clip_path": clip_path,
            "start_img": start_img, "prompt": prompt, "dur": dur,
            "vo_path": vo_path, "vo_secs": vo_secs,
            "beat": beat, "insert_beats": insert_beats, "who": who,
            "used_pro_fallback": used_pro_fallback,
            "type_label": sc.get("type", "scene").upper(),
        })

    # --- Render every queued clip IN PARALLEL ------------------------------------------------
    # The loop above composed each scene image (serial, so the room anchor stays consistent) and
    # queued a render task per scene that needs a new clip. Now fire those Seedance jobs together,
    # up to MAX_PARALLEL_RENDERS at a time, instead of waiting for each to finish before starting
    # the next — that serial wait is what made the render step take ~23 minutes. The clips finish
    # in whatever order they're ready; as each lands we total its cost and set the scene's beats
    # HERE on the main thread, so the workers never touch shared state.
    if render_tasks:
        workers = min(MAX_PARALLEL_RENDERS, len(render_tasks))
        word = "clip" if len(render_tasks) == 1 else "clips"
        print(f"Rendering {len(render_tasks)} {word} in parallel (up to {workers} at a time; "
              f"they finish as they're ready) ...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_render_task, t) for t in render_tasks]
            for fut in as_completed(futures):
                r = fut.result()
                t, sc, i = r["task"], r["task"]["sc"], r["task"]["i"]
                if not r["ok"]:
                    print(f"  [{i}] {t['type_label']} skipped: Seedance could not render it.")
                    continue
                scene_video_seconds += r["secs"]
                sc["clip_seconds"] = r["secs"]     # store billed length so a later reuse prices it exactly
                sc["beats"] = t["insert_beats"] + [t["beat"]]
                if DRAFT:
                    print(f"  [{i}] {t['type_label']} [{t['who']}] -> {t['clip_name']} (DRAFT)")
                else:
                    vo_note = (f" over {t['vo_secs']:.1f}s voiceover" if t["vo_secs"] > 0
                               else " (no voiceover)")
                    print(f"  [{i}] {t['type_label']} [{t['who']}] -> {t['clip_name']} "
                          f"(silent {t['dur']}{vo_note}){' + detail' if t['insert_beats'] else ''}"
                          f"{'  [image: PRO fallback, $0.15]' if t['used_pro_fallback'] else ''}")

    # --- Cost: this step pays for TWO things only (the audio was paid in step 6):
    #   1) fal Seedance      - the silent scene videos ($0.026/s, half price with no audio)
    #   2) fal Nano Banana 2 - the composed scene images ($0.08 each; Pro fallback tops up)
    # Each is priced twice: the TRUE total (this run's pieces + the ones reused from a prior
    # run) for the video's real price, and SPENT (this run's pieces only) for what we paid now.
    _, silent_rate = costs.seedance_rates()

    def _image_price(n_images: int, n_pro: int) -> float:
        # Every composed image costs the NB2 rate; each one that fell back to Pro (NB2 returned
        # nothing, so NB2 billed $0) is topped up by the gap so its true cost is Pro's.
        return (n_images * costs.NANO_BANANA_2_EDIT_PER_IMAGE
                + n_pro * (costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
                           - costs.NANO_BANANA_2_EDIT_PER_IMAGE))

    # TRUE total = this run's pieces + everything reused from disk (paid on an earlier run).
    total_video_seconds = scene_video_seconds + reused_video_seconds
    total_images = scene_images + reused_images
    total_pro = _pro_fallbacks + reused_pro
    scene_cost = total_video_seconds * silent_rate
    detail_cost = detail_video_seconds * silent_rate
    image_cost = _image_price(total_images, total_pro)
    video_cost = scene_cost + detail_cost + image_cost

    # SPENT this run = only the pieces we regenerated now (reused ones cost $0 today).
    scene_spent = scene_video_seconds * silent_rate
    image_spent = _image_price(scene_images, _pro_fallbacks)

    # Record each piece under its OWN key so the final table lists them one by one; the label
    # shows the video's real numbers (total), and spent drives the "you paid this run" line.
    fb_note = f" (incl. {total_pro} Pro fallback)" if total_pro else ""
    costs.record(data, "scene_video",
                 f"Scene videos - fal Seedance silent ({total_video_seconds:.0f} seconds)",
                 scene_cost, spent=scene_spent)
    costs.record(data, "scene_images",
                 f"Scene images - fal Nano Banana 2 ({total_images} images){fb_note}",
                 image_cost, spent=image_spent)
    if detail_cost:
        costs.record(data, "detail_video",
                     f"Detail inserts - fal Seedance silent ({detail_video_seconds:.0f} seconds)", detail_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    video_spent = scene_spent + image_spent + detail_cost   # what fal charged us THIS run
    n_inserts = sum(1 for v in detail_by_loc.values() if v)
    fb = f", {total_pro} Pro fallback" + ("s" if total_pro != 1 else "") if total_pro else ""
    print(f"\nUpdated output/analysis.json with the scene beats. "
          f"({total_images} images{fb}, {n_inserts} detail inserts)")
    # Print each piece for this step, so nothing is hidden inside one number. The numbers are
    # the WHOLE video's (this run's pieces + any reused from a prior run), matching the final
    # cost table; then, if a repair/redo reused some, show what this run actually paid.
    print("\n  Video cost — each piece (whole video):")
    print(f"    Scene videos    (fal Seedance, silent): {total_video_seconds:.0f} seconds "
          f"x ${silent_rate}/second = ${scene_cost:.4f}")
    print(f"    Scene images    (fal Nano Banana 2):    {total_images} images "
          f"x ${costs.NANO_BANANA_2_EDIT_PER_IMAGE}/image = ${image_cost:.4f}{fb_note}")
    if detail_cost:
        print(f"    Detail inserts  (fal Seedance, silent): {detail_video_seconds:.0f} seconds "
              f"x ${silent_rate}/second = ${detail_cost:.4f}")
    if video_cost - video_spent > 0.0001:
        print(f"    -> whole video: ${video_cost:.4f}; paid THIS run: ${video_spent:.4f} "
              f"(the rest was reused at $0)")
    # The per-step COST line reports what fal charged THIS run (reused pieces cost $0 now),
    # so it matches your actual spend; the final table still carries the whole-video price.
    costs.show(f"Video made this run ({len(scenes)} scenes: Seedance video + Nano Banana images)",
               video_spent)


if __name__ == "__main__":
    main()
