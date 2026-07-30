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
Cost:   Veo 3.1 fast — $0.10/s via Google (720p, default) or $0.15/s via fal —
        + ElevenLabs Speech-to-Speech ~$0.002/s + $0.15 per composed scene image
        + ~$0.002/s ambient beds.
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
# Nano Banana (scene composition) and ElevenLabs (re-voice) still run on every path, so
# their keys are always required — regardless of which backend renders the Veo clips.
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVEN_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty (needed to re-voice into our voices).")

# Which backend renders the Veo SCENE clips (our biggest cost). "google" (default) calls
# Veo 3.1 fast through Google's own Gemini API at $0.10/s (720p) — the SAME model fal
# serves, just ~33% cheaper — which is the single largest saving in the pipeline. "fal"
# keeps the original path. Only the video call moves; images and voices are unchanged.
VEO_PROVIDER = os.getenv("VEO_PROVIDER", "google").lower()
GOOGLE_KEY = os.getenv("GOOGLE_API_KEY")
if VEO_PROVIDER == "google" and not GOOGLE_KEY:
    sys.exit("ERROR: VEO_PROVIDER=google but GOOGLE_API_KEY is empty. Paste a "
             "BILLING-ENABLED Google AI Studio key in .env (Veo is NOT on the free "
             "tier), or set VEO_PROVIDER=fal to use the old path.")

OUT_DIR = "output"
VEO_MODEL = "fal-ai/veo3.1/fast/image-to-video"    # fal path: two-person scene + lip-sync
VEO_MODEL_GOOGLE = "veo-3.1-fast-generate-preview"  # google path: same Veo 3.1 fast, i2v
SCENE_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"  # compose chars into one shot
IMAGE_MODEL = "fal-ai/nano-banana-pro"            # text-to-image (detail with no room ref)
STS_MODEL = "eleven_english_sts_v2"               # ElevenLabs voice changer (keeps timing)
# Resolution differs by backend. On fal, 1080p cost the SAME per second as 720p, so we
# took the sharper one for free. On Google, 1080p costs MORE and forces every clip to a
# full 8s block (see costs.py), so the cheap tier is 720p — the editor upscales it to
# 1080x1920. Pick the right one for the backend in use.
VEO_RES = "720p" if VEO_PROVIDER == "google" else "1080p"

_genai_client = None


def _google_client():
    """Lazily build ONE Gemini API client, only when the google Veo path is actually
    used, so a fal-only run never has to import google-genai."""
    global _genai_client
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(api_key=GOOGLE_KEY)
    return _genai_client

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
REACTION_DUR = "4s"       # Veo's shortest block; the editor caps silent beats short
# DETAIL INSERT: at each NEW location we open on a short, silent, face-free shot of one
# object that says where we are (a gavel, a nameplate, a case bundle) — the modern
# replacement for the establishing wide. It orients the viewer before the scene starts,
# and because it has no faces nothing can drift. One Nano image ($0.15) + one 4s silent
# Veo clip ($0.40) per location, cached so we pay once per place. This is real footage,
# not a frozen still — that is why the old still-zoom establishing beat was removed.
DETAIL_INSERTS = True
DETAIL_DUR = "4s"         # Veo's shortest block; the editor trims it to a ~1.2s glance


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

    WHY: feeding the entire GRID sheet into nano-banana/edit made the compositor ECHO the
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
                           "aspect_ratio": "9:16", "resolution": "2K", "num_images": 1},
                with_logs=False,
            )
        else:
            r = fal_client.subscribe(
                IMAGE_MODEL,       # no room to anchor to -> compose the object from text
                arguments={"prompt": common, "aspect_ratio": "9:16",
                           "resolution": "2K", "num_images": 1},
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
    image (Nano) and animate it as a short SILENT Veo clip. Returns
    (beat_or_None, images_paid, veo_seconds). Resume-guarded on both the image and the
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
    secs = make_veo_clip(img, detail_insert_prompt(detail, setting), DETAIL_DUR,
                         clip_path, generate_audio=False)
    if secs == 0.0 or not os.path.exists(clip_path):
        return None, images_paid, 0.0
    return beat, images_paid, secs


def detail_insert_prompt(detail: str, setting: str) -> str:
    """Veo prompt for the silent detail beat: a slow, quiet push-in on the object, no
    people, tiny real-world motion so it reads as footage rather than a frozen still."""
    obj = (detail or "the object").strip().rstrip(".")
    place = (setting or "the room").strip().rstrip(".")
    return (f"A slow, quiet cinematic push-in on {obj} in {place}. NObody in frame — no "
            f"person, no hands, no face — only the object. Tiny ambient motion (a slight "
            f"drift of light, dust, or the object settling), the scene already in gentle "
            f"movement from the first frame. {STYLE}. No text.")


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


# A Veo generation is the one call we CANNOT afford to lose: it is billed the moment
# fal starts it, and the result only reaches us if we are still listening. fal_client's
# subscribe() polls the queue in a loop with no overall deadline, so a stalled poll
# leaves it waiting forever (it hung a real run for 23 minutes on a dead connection).
# We therefore submit the job ourselves and poll with our own deadline. Crucially we
# record the request_id on disk BEFORE waiting: a job we already paid for can then be
# collected on the next run instead of being submitted — and paid for — a second time.
VEO_MAX_WAIT = 600        # give one Veo job this long to finish before we stop waiting
VEO_POLL_EVERY = 5        # ask the queue for its status this often
VEO_JOBS = os.path.join(OUT_DIR, "_veo_jobs.json")               # fal:    out_file -> request_id
VEO_JOBS_GOOGLE = os.path.join(OUT_DIR, "_veo_jobs_google.json")  # google: out_file -> operation name


def _veo_jobs(store: str = VEO_JOBS) -> dict:
    """The saved out_file -> job-id map of Veo jobs we have paid to start. Kept in a
    SEPARATE file per backend (fal request_id vs Google operation name) so the two id
    formats can never be mistaken for one another across a provider switch."""
    try:
        with open(store) as f:
            return json.load(f)
    except Exception:
        return {}


def _remember_veo_job(out_path: str, job_id: str, store: str = VEO_JOBS):
    """Record a submitted job so a later run can collect it for free."""
    jobs = _veo_jobs(store)
    jobs[os.path.basename(out_path)] = job_id
    with open(store, "w") as f:
        json.dump(jobs, f, indent=2)


def _forget_veo_job(out_path: str, store: str = VEO_JOBS):
    """Drop a job once its video is safely on disk (or is permanently dead)."""
    jobs = _veo_jobs(store)
    if jobs.pop(os.path.basename(out_path), None) is not None:
        with open(store, "w") as f:
            json.dump(jobs, f, indent=2)


def _save_veo_video(result: dict, out_path: str) -> bool:
    """Download the finished video out of a fal result payload."""
    video = result.get("video")
    url = video["url"] if isinstance(video, dict) else video
    if not url:
        return False
    with open(out_path, "wb") as f:
        f.write(requests.get(url, timeout=180).content)
    return os.path.exists(out_path)


def _await_veo(handle, out_path: str) -> bool:
    """Wait for one submitted Veo job, polling with OUR deadline so a stalled queue
    poll can never hang the run. A failed poll is ignored and retried — the job keeps
    running on fal's side regardless of whether we are listening. Returns True if the
    video landed on disk."""
    deadline = time.time() + VEO_MAX_WAIT
    while time.time() < deadline:
        try:
            if isinstance(handle.status(), fal_client.Completed):
                return _save_veo_video(handle.get(), out_path)
        except Exception:
            pass          # transient poll failure — the job is unaffected, just retry
        time.sleep(VEO_POLL_EVERY)
    return False


def make_veo_clip(image_path: str, prompt: str, duration: str, out_path: str,
                  generate_audio: bool = True) -> float:
    """Render one Veo 3.1 fast clip from the start image, on whichever backend
    VEO_PROVIDER selects. Both paths return the billed seconds (0.0 on failure, writing
    no file, so one bad beat never crashes the run) and both are crash-safe: the job id
    is saved to disk BEFORE we wait, so a killed run collects the already-paid clip next
    time instead of paying twice. Google is the cheaper default ($0.10/s vs $0.15/s)."""
    if VEO_PROVIDER == "google":
        return _make_veo_clip_google(image_path, prompt, duration, out_path, generate_audio)
    return _make_veo_clip_fal(image_path, prompt, duration, out_path, generate_audio)


def _make_veo_clip_google(image_path: str, prompt: str, duration: str, out_path: str,
                          generate_audio: bool = True) -> float:
    """Animate the start image with Veo 3.1 fast via Google's Gemini API. Same model +
    native lip-sync as the fal path, but Google prices it at $0.10/s (720p). Crash-safety
    mirrors the fal path: Veo bills the moment Google starts the job and the result only
    reaches us while we hold the operation, so we persist the operation NAME on disk
    BEFORE polling — a run killed mid-wait collects the paid video next time for free."""
    from google.genai import types
    client = _google_client()
    secs = float(int(duration[:-1]))

    # First collect anything a previous run already paid for: a job it submitted and then
    # died waiting on kept running on Google's side, so take that video instead of re-paying.
    prior = _veo_jobs(VEO_JOBS_GOOGLE).get(os.path.basename(out_path))
    if prior:
        try:
            op = client.operations.get(types.GenerateVideosOperation(name=prior))
            status = _await_veo_google(client, op, out_path)
            if status == "ok":
                _forget_veo_job(out_path, VEO_JOBS_GOOGLE)
                if not generate_audio:
                    _mute_clip(out_path)
                print("    (collected the Veo clip a previous run already paid for, $0)")
                return secs
            if status == "timeout":
                print("    (the earlier Veo job is still rendering; re-run later to collect it)")
                return 0.0
            # blocked/failed: the earlier job produced NO video, so it is dead — drop it
            # (there is nothing to collect) and generate a fresh one below.
            _forget_veo_job(out_path, VEO_JOBS_GOOGLE)
            print("    (the earlier Veo job produced no video; regenerating)")
        except Exception as e:
            print(f"    could not collect the earlier Veo job ({_short_err(e)}); re-submitting")

    try:
        start = types.Image.from_file(location=image_path)
    except Exception as e:
        print(f"    veo could not read start image ({_short_err(e)})")
        return 0.0

    # NOTE on audio: the Gemini Developer API (API-key mode) does NOT accept the
    # generate_audio parameter — Veo 3.1 here ALWAYS renders its native audio, and
    # passing the flag is rejected outright. So we never send it. For beats that must be
    # SILENT (face-free detail inserts, which must not carry Veo's invented sound), we
    # strip the audio locally after download instead of asking the API to skip it.
    cfg = types.GenerateVideosConfig(
        aspect_ratio="9:16",              # vertical TikTok
        resolution=VEO_RES,               # 720p — the $0.10/s tier
        duration_seconds=int(duration[:-1]),
        number_of_videos=1,
        # Our start image already contains adults, so allow people — otherwise a
        # face-generation default could reject a dialogue clip outright.
        person_generation="allow_adult",
    )
    for attempt in range(1, 4):
        try:
            op = client.models.generate_videos(
                model=VEO_MODEL_GOOGLE, prompt=prompt, image=start, config=cfg)
            # Save the operation name BEFORE waiting: from here the job is billable and
            # must be recoverable even if this process is killed mid-wait.
            _remember_veo_job(out_path, op.name, VEO_JOBS_GOOGLE)
            print(f"    veo rendering {duration} clip (job {op.name.split('/')[-1][:8]}, "
                  f"up to {VEO_MAX_WAIT // 60} min) ...")
            status = _await_veo_google(client, op, out_path)
            if status == "ok":
                _forget_veo_job(out_path, VEO_JOBS_GOOGLE)
                # Google always bakes in Veo's audio; if this beat is meant to be silent,
                # replace that track with silence now (keeps detail inserts truly silent).
                if not generate_audio:
                    _mute_clip(out_path)
                return secs
            if status == "timeout":
                # A GENUINE timeout: still rendering when our deadline hit. Keep the job id
                # so the next run collects the finished video instead of paying again.
                print(f"    veo attempt {attempt}: still rendering after {VEO_MAX_WAIT // 60} "
                      "min; job saved, re-run to collect it without paying again")
                return 0.0
            # 'blocked' (safety/likeness filter) or 'failed': this generation produced NO
            # video, so the saved job is dead — drop it (nothing to collect). The real
            # reason was already printed by _await_veo_google. Google's likeness filter is
            # INCONSISTENT (the same face passes on one clip, is blocked on another) and a
            # blocked generation is not billed, so try again with a fresh generation.
            _forget_veo_job(out_path, VEO_JOBS_GOOGLE)
            if attempt < 3:
                print(f"    veo attempt {attempt} made no video ({status}); retrying ...")
                time.sleep(3)
                continue
            print(f"    veo gave up on this beat after {attempt} attempts ({status}).")
            return 0.0
        except Exception as e:
            msg = _short_err(e)
            print(f"    veo attempt {attempt} failed: {msg}")
            if "content filter" in msg or "content_policy" in msg or "blocked" in msg.lower():
                return 0.0          # same wording will fail again — give up on this beat
            time.sleep(5)
    return 0.0


def _veo_filter_reason(op) -> str:
    """The human-readable reason Google's safety/likeness filter blocked a generation, or
    "" if it was not filtered. Veo signals a block as done=True, NO error and NO video,
    with the reason tucked into response.rai_media_filtered_reasons — which otherwise
    reaches us as a mysterious 'could not generate'. Reading it is what makes the failure
    debuggable (e.g. "we can't create videos with real people's names or likenesses")."""
    resp = getattr(op, "response", None) or getattr(op, "result", None)
    if resp is None:
        return ""
    reasons = getattr(resp, "rai_media_filtered_reasons", None)
    if reasons:
        return "; ".join(str(r) for r in reasons)
    if getattr(resp, "rai_media_filtered_count", 0):
        return "content blocked by Veo's safety/likeness filter (no reason text returned)"
    return ""


def _await_veo_google(client, op, out_path: str) -> str:
    """Poll one Google Veo operation with OUR deadline (a stalled poll can never hang the
    run), refreshing it until done. Returns a STATUS so the caller can act correctly:
      'ok'      – the finished video was saved to out_path
      'blocked' – Google's safety/likeness filter refused it (the real reason is printed)
      'failed'  – the job errored, or finished with no video for an unknown reason
      'timeout' – our deadline passed before it finished (it may still be rendering)
    The old code returned a bare False for the last three, and the caller mislabelled every
    one as a 'timeout' — hiding the real reason and falsely promising a free re-collect."""
    deadline = time.time() + VEO_MAX_WAIT
    while time.time() < deadline:
        try:
            op = client.operations.get(op)
        except Exception:
            time.sleep(VEO_POLL_EVERY)     # transient — job unaffected, retry
            continue
        if op.done:
            if getattr(op, "error", None):
                print(f"    veo job failed: {str(op.error)[:160]}")
                return "failed"
            reason = _veo_filter_reason(op)
            if reason:
                print(f"    veo BLOCKED by Google's safety/likeness filter: {reason}")
                return "blocked"
            if _save_veo_video_google(client, op, out_path):
                return "ok"
            print("    veo finished but returned no video and no reason (unknown failure)")
            return "failed"
        time.sleep(VEO_POLL_EVERY)
    return "timeout"


def _save_veo_video_google(client, op, out_path: str) -> bool:
    """Download the finished MP4 out of a completed Google Veo operation."""
    resp = getattr(op, "response", None)
    vids = getattr(resp, "generated_videos", None) if resp else None
    if not vids:
        return False
    vid = vids[0].video
    try:
        client.files.download(file=vid)    # populate the bytes (Gemini API)
    except Exception:
        pass                               # bytes may already be inline; fall through
    data = getattr(vid, "video_bytes", None)
    if data:
        with open(out_path, "wb") as f:
            f.write(data)
    else:
        vid.save(out_path)                 # SDK writes the downloaded bytes to disk
    return os.path.exists(out_path)


def _mute_clip(path: str) -> bool:
    """Replace a clip's audio track with silence, in place. Used only on the Google path
    for silent beats: the Gemini Developer API always bakes Veo's native audio in, so we
    swap it for a silent stereo track here. We keep a (silent) track rather than dropping
    audio entirely so every beat still has a matching stream for the editor's concat."""
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


def _make_veo_clip_fal(image_path: str, prompt: str, duration: str, out_path: str,
                       generate_audio: bool = True) -> float:
    """Animate the start image into a clip with Veo 3.1 fast on FAL. For dialogue lines we
    keep generate_audio=True (Veo's own voice + native lip-sync, which we later
    re-voice). For SILENT beats (reaction cutaways) we pass generate_audio=False so
    Veo doesn't invent a voice we'd have to strip. Returns the billed seconds, or
    0.0 (and writes no file) if Veo could not make it — so one bad beat never
    crashes the run. Retries transient errors; a content-filter block is permanent
    for that wording, so we stop retrying it (auto_fix already had its one shot)."""
    # First: collect anything we already paid for. If a previous run submitted this
    # clip and died waiting, the job kept running on fal — take that video for free
    # rather than paying to generate the same thing again.
    prior = _veo_jobs().get(os.path.basename(out_path))
    if prior:
        try:
            handle = fal_client.SyncRequestHandle.from_request_id(
                fal_client.sync_client._client, VEO_MODEL, prior)
            if isinstance(handle.status(), fal_client.Completed):
                if _save_veo_video(handle.get(), out_path):
                    _forget_veo_job(out_path)
                    print("    (collected the Veo clip a previous run already paid for, $0)")
                    return float(int(duration[:-1]))
            elif _await_veo(handle, out_path):     # still running — wait for it
                _forget_veo_job(out_path)
                print("    (collected the Veo clip a previous run already paid for, $0)")
                return float(int(duration[:-1]))
        except Exception as e:
            print(f"    could not collect the earlier Veo job ({_short_err(e)}); re-submitting")

    image_url = fal_client.upload_file(image_path)
    for attempt in range(1, 4):
        try:
            handle = fal_client.submit(
                VEO_MODEL,
                arguments={"prompt": prompt, "image_url": image_url,
                           "duration": duration, "resolution": VEO_RES,
                           "generate_audio": generate_audio, "aspect_ratio": "9:16",
                           "auto_fix": True},   # let Veo soften a borderline prompt itself
            )
            # Save the id BEFORE waiting: from here on the job is billable, so it must
            # be recoverable even if this process is killed mid-wait.
            _remember_veo_job(out_path, handle.request_id)
            print(f"    veo rendering {duration} clip (job {handle.request_id[:8]}, "
                  f"up to {VEO_MAX_WAIT // 60} min) ...")
            if _await_veo(handle, out_path):
                _forget_veo_job(out_path)
                return float(int(duration[:-1]))
            # Timed out. Keep the job id — the next run collects it instead of re-paying.
            print(f"    veo attempt {attempt} timed out after {VEO_MAX_WAIT // 60} min; "
                  "job saved, re-run to collect it without paying again")
            return 0.0
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
        # is REAL in the composed frame and the video camera then stays locked on it, Veo
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
    as an explicit TIMELINE. Each line gets its own time window, but the CAMERA stays
    ONE locked two-shot for the whole clip — no internal cuts or over-the-shoulder
    reverse angles. That reframe used to duplicate a person into the foreground (the
    same character appeared twice mid-clip); a fixed frame can't, so coverage comes from
    the editor's zoom and the cuts between scenes instead. The per-line windows plus the
    'replies' hand-off still stop Veo from voicing both lines at once and mumbling a
    garbled bridge between them. Delivery cue and micro-expression ride inside each line
    so the faces perform."""
    lines = sc.get("dialogue", [])
    speakers = sc.get("characters", [])
    # Everyone in the frame, not just the talkers: the silent people are composed into
    # the start image, so the prompt must account for them or Veo drops or redraws them.
    onscreen = [n for n in (sc.get("onscreen") or speakers)] or speakers
    who = {n: descriptor(chars_by_name.get(n, {})) for n in onscreen}
    wins, total = _line_windows(lines)
    veo_secs = int(scene_veo_duration(lines)[:-1])
    # Only ever SHRINK the windows to fit the clip — never stretch them to fill it.
    # Stretching pads each line with time it has no words for, and Veo fills that gap
    # with invented mumbling; because the padding lands at the end of a line, the
    # garble comes out exactly on the cut to the next speaker (heard as a "monster
    # voice" mid-scene). Any leftover time is pushed to the END of the clip instead,
    # where a held silent reaction is harmless — and is asked for explicitly below.
    if total > veo_secs and total > 0:
        scale = veo_secs / total
        wins = [[s * scale, e * scale] for s, e in wins]
        total = veo_secs
    # Whatever time is left after the last word is spoken: name it, so Veo plays it as
    # deliberate silence rather than treating it as space that needs filling with sound.
    tail = max(0.0, veo_secs - total)
    # ONE locked framing for the WHOLE clip — deliberately NOT a per-line shot change.
    # We used to ask Veo for internal CUTS (an over-the-shoulder REVERSE angle on each
    # reply) to fake real coverage, but that is exactly what CLONES a person: to build an
    # over-the-shoulder Veo needs a body's shoulder in the foreground, and since both
    # people are already seated in frame it DUPLICATES one of them to supply it (the same
    # woman appeared twice mid-clip). A single, stable two-shot never needs a foreground
    # body, so it cannot clone. The sense of coverage now comes from the editor's slow zoom
    # and the hard cuts BETWEEN scenes — never from a second camera move inside one clip.
    n_on = len(onscreen)
    hold_shot = ("a steady over-the-shoulder shot — one person's shoulder held in the "
                 "foreground, the other facing the camera in sharp focus" if n_on <= 2 else
                 f"a steady medium wide {n_on}-shot holding everyone in the room")
    rows = []
    for j, d in enumerate(lines):
        s, e = wins[j]
        spk_name = d.get("character", "")
        spk = who.get(spk_name, "the other person").capitalize()
        tone = f" ({d['emotion'].strip()})" if d.get("emotion") else ""
        lead = (f"{spk} says{tone}" if j == 0
                else f"{_reply_cue(chars_by_name.get(spk_name, {}))}{tone}")
        # Only WHO speaks WHEN — no per-line camera change (that reframe caused the clone).
        rows.append(f"{s:.1f}-{e:.1f}s: {lead}: \"{d.get('line', '')}\".")
    # CAST block: pin EVERY on-screen person to their locked identity (hair, build,
    # CLOTHING COLOUR) so the reverse angle can't redraw the wrong face or recolour an
    # outfit. Silent people need this as much as speakers — they're on camera too.
    cast = " ".join(f"{who.get(n, 'a person').capitalize()} = "
                    f"{identity_lock(chars_by_name.get(n, {}))}." for n in onscreen)
    # Name the people who never speak, so Veo keeps them present and reacting instead
    # of treating them as scenery to drift, mute-swap, or quietly drop out of frame.
    silent = [who[n] for n in onscreen if n not in speakers]
    silent_line = ""
    if silent:
        verb = "stays" if len(silent) == 1 else "stay"
        silent_line = (f" {', '.join(silent).capitalize()} {verb} in the room throughout, "
                       "silent — never speaking or moving their mouth, listening and "
                       "reacting with their face.")
    # The scene's blocking, so the shot has a concrete physical action, not just heads.
    action = (sc.get("action") or "").strip().rstrip(".")
    action_line = f" {action}." if action else ""
    # Spell out the silent tail as its own timeline row, so the leftover seconds read as
    # a directed beat ("hold, nobody speaks") instead of unexplained empty runtime.
    tail_line = ""
    if tail >= 0.4:
        tail_line = (f"\n{total:.1f}-{veo_secs:.1f}s: Hold on the faces in silence — "
                     "nobody speaks, no words at all, just the reaction settling.")
    # "Facing each other" only makes sense for a pair; a group turns toward whoever
    # holds the floor. Either way, nobody looks down the lens (that's the narrator's job).
    if len(onscreen) <= 2:
        staging = ("two people mid-conversation in an over-the-shoulder framing — the person "
                   "facing the camera looks at the foreground person beside the lens, never "
                   "straight into the lens")
    else:
        staging = (f"{len(onscreen)} people in one conversation, turned toward whoever "
                   "is speaking, never the camera")
    return (
        # Lead with motion so Veo doesn't open on a frozen staring frame.
        f"{veo_secs} seconds / ONE continuous LOCKED shot, no cuts / intimate cinematic "
        f"legal drama / no music.\n"
        f"A continuous cinematic scene, already in motion from the first frame — "
        f"{staging}. "
        # ONE fixed camera for the whole clip. The over-the-shoulder/reverse reframe is
        # banned here because building it makes Veo duplicate a person into the foreground.
        f"CAMERA: {hold_shot}, held as ONE fixed angle for the entire clip with at most a "
        f"very slight, slow push-in. The camera does NOT cut, swing, orbit, or change to a "
        f"different angle — it holds this single framing the whole time. The foreground "
        f"person keeps their back to the camera throughout; do NOT turn them around, and do "
        f"NOT add, duplicate or reveal any second copy of anyone. "
        f"{STYLE}, natural room ambience.\n"
        f"CAST: {cast}\n"
        "TIMELINE:\n" + "\n".join(rows) + tail_line +
        "\nNobody speaks except in their own window above — no extra words, no muttering, "
        "no invented dialogue between the lines. Only the person whose line it is moves "
        f"their mouth; the others listen and react.{silent_line}{action_line} "
        # Veo likes to invent big moves, and it stages them from nothing: a seated
        # person "stands" out of a chair that was never rendered, or furniture pops in
        # and out. Pin the blocking to what the start frame actually shows.
        "Everyone keeps the position the opening frame puts them in — whoever is seated "
        "stays seated, whoever is standing stays standing; nobody stands up, sits down or "
        "walks off, and no furniture or object appears, vanishes or changes place. "
        "Nuanced facial micro-expressions, real weighty movement and object physics, "
        "every person's face and clothing colour identical throughout, movie-level subtlety. "
        # Veo tends to briefly CLONE a person mid-clip when the camera reframes (a second
        # copy of the same person flickers in during the angle change, then resolves). Name
        # it so the model holds one stable body per person through the move.
        "Exactly one of each person on screen at all times — as the camera moves or changes "
        "angle, NEVER duplicate, clone, split, mirror or ghost a person; keep stable, "
        "continuous geometry with the same single body for each person throughout."
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
    detail_veo_seconds = 0.0  # Veo seconds for silent detail inserts (billed $0.10/s, no audio)
    sts_seconds = 0.0       # ElevenLabs Speech-to-Speech seconds
    ambient_seconds = 0.0   # ElevenLabs Sound-Effects seconds (ambient beds + music)
    scene_images = 0        # composed images we paid for (scene composites + details)
    location_ref = {}       # setting -> first image made there (keep the room identical)
    compose_cache = {}      # (same people, same place) -> reuse that image (no re-pay)
    coverage_cache = {}     # (same people, same place) -> reuse the angle set (no re-pay)
    ambient_by_loc = {}     # setting -> looping ambient bed made there (reuse, no re-pay)
    detail_by_loc = {}      # setting -> detail insert beat made there (reuse, no re-pay)

    # ONE looping underscore for the whole video (see make_music). Reused every run; the
    # editor loops and ducks it under the dialogue. A failure just means no music.
    music_path = os.path.join(OUT_DIR, "music.mp3")
    if os.path.exists(music_path):
        print("    (reusing music bed music.mp3 — already on disk, $0)")
    else:
        ambient_seconds += make_music(music_path)

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
                    compose_shot = (group_shot(names, chars_by_name, sc.get("shot", ""))
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

        # Detail insert: the FIRST time we enter a location, open on a silent, face-free
        # shot of one identifying object, so the viewer is oriented before the scene
        # starts. Made once per location (detail_by_loc), then prepended to this scene's
        # beats. Same-location continuations get nothing. Marked seen even on failure so
        # we don't retry it every scene.
        insert_beats = []
        if loc_key not in detail_by_loc:
            detail_by_loc[loc_key] = None
            ins, imgs, dsecs = make_detail_insert(
                i, sc.get("detail", ""), sc_setting, room_ref or start_img)
            scene_images += imgs
            detail_veo_seconds += dsecs
            if ins:
                detail_by_loc[loc_key] = ins
                insert_beats = [ins]

        dialogue_beat = {"file": clip_name, "kind": "dialogue",
                         "speaker": None, "silent": False}

        # Resume guard: a finished scene clip on disk is reused for free.
        if os.path.exists(clip_path):
            sc["beats"] = insert_beats + [dialogue_beat]
            print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {clip_name} "
                  f"(reused, already on disk, $0){' + detail' if insert_beats else ''}")
            continue

        # DRAFT: shortest length + no audio, to preview framing/identity cheaply.
        dur = "4s" if DRAFT else scene_veo_duration(sc.get("dialogue", []))
        secs = make_veo_clip(start_img, build_scene_prompt(sc, chars_by_name),
                             dur, clip_path, generate_audio=not DRAFT)
        if secs == 0.0 or not os.path.exists(clip_path):
            print(f"  [{i}] DIALOGUE skipped: Veo could not generate it.")
            continue
        veo_seconds += secs
        # Beats = optional detail insert, then the whole scene. The dialogue clip carries
        # real lip-synced speech, so the editor keeps its audio locked to its own video.
        sc["beats"] = insert_beats + [dialogue_beat]
        print(f"  [{i}] DIALOGUE [{' + '.join(speakers)}] -> {clip_name} "
              f"(one continuous clip, Veo {secs:.0f}s, native voices)"
              f"{' + detail' if insert_beats else ''}")

    # --- Cost: dialogue/narration Veo + silent detail Veo + voice swap + sound + images ---
    # Rates depend on the backend that actually rendered the clips (Google is cheaper and
    # bundles audio; fal is dearer but discounts silent clips) — see costs.veo_rates.
    veo_rate, detail_rate = costs.veo_rates(VEO_PROVIDER)
    veo_cost = veo_seconds * veo_rate
    detail_veo_cost = detail_veo_seconds * detail_rate
    sts_cost = sts_seconds * costs.ELEVEN_STS_PER_SEC
    sfx_cost = ambient_seconds * costs.ELEVEN_SFX_PER_SEC                   # ambient beds + music
    image_cost = scene_images * costs.NANO_BANANA_PRO_EDIT_PER_IMAGE
    clip_cost = veo_cost + detail_veo_cost + sts_cost + sfx_cost + image_cost
    costs.record(data, "clips",
                 f"Scenes - Veo 3.1 fast ({veo_seconds:.0f}s) + detail inserts "
                 f"({detail_veo_seconds:.0f}s silent) + voice swap ({sts_seconds:.0f}s) + "
                 f"{scene_images} images + {ambient_seconds:.0f}s sound",
                 clip_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    n_inserts = sum(1 for v in detail_by_loc.values() if v)
    print(f"\nUpdated output/analysis.json with the scene beats. "
          f"({scene_images} images, {len(ambient_by_loc)} ambient beds, {n_inserts} detail inserts)")
    costs.show(f"{len(scenes)} scenes (Veo {veo_seconds:.0f}s + {detail_veo_seconds:.0f}s detail "
               f"+ voice swap {sts_seconds:.0f}s + {scene_images} images + sound)", clip_cost)


if __name__ == "__main__":
    main()
