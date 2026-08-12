"""Compose each scene's IMAGE — the picture half of the scene step (memoir pipeline).

This file owns everything that makes the STILL image of a scene with Nano Banana 2: laying the
locked character portraits into one shot, keeping the room identical across a location, and the
Pro-fallback when the cheap tier returns nothing. The VIDEO half (animating these images into
clips with Seedance) lives in scene_video.py; the step that drives both is scene_clips.py.

It also holds the small SHARED helpers both halves need (slug, descriptor, identity_lock, the
STYLE look, OUT_DIR, the fal-error shortener), because the image side is the lower-level half —
scene_video.py and scene_clips.py import these from here.

Cost: Nano Banana 2 compose $0.08 / image at 1K (Pro fallback $0.15 on the few that need it).
"""
import os
import sys
import re
import time
import subprocess
import requests
import fal_client
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
# Nano Banana (image compose) and Seedance (video) both run on fal, so the key is always needed.
# This check runs for the whole scene step because scene_video/scene_clips import this module.
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty. Open .env and paste your fal.ai key.")

OUT_DIR = "output"
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

# ONE shared visual look, dropped into EVERY prompt — the character sheet, the composed
# scene image, and every Seedance clip. Reusing the exact same palette/grain/lens wording is
# what makes separate generations read as a single film instead of clips from different
# cameras, and it softens the jump between scenes. Kept in one place so it can never
# drift out of sync between the steps.
STYLE = ("shot on 35mm film, muted teal-and-amber palette, soft cinematic grain, "
         "shallow depth of field, moody prestige legal-drama lighting, photorealistic")

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
                        people: list = None) -> tuple:
    """Compose the given character portraits into ONE upright vertical scene image
    (Nano Banana 2 edit). Keeps their exact faces. Returns (ok, used_pro): ok is True on
    success, used_pro is True if the compose fell back to Nano Banana PRO (so the caller can
    price it right). Returning the flag — rather than bumping a global — is what makes this
    SAFE TO CALL FROM PARALLEL THREADS.

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
    #    No Pro fallback used yet, so the second element of every early return is False.
    for attempt in range(1, 3):
        p = prompt if attempt == 1 else prompt + " CRITICAL: upright vertical frame."
        try:
            if _compose_once(SCENE_EDIT_MODEL, p, {}):
                return True, False
            print(f"    scene image came out landscape; retrying ({attempt})")
        except Exception as e:
            print(f"    compose attempt {attempt} (non-pro) failed ({e}); retrying in 5s ...")
            time.sleep(5)
    # 2) Pro fallback: non-pro gave nothing usable, so pay for ONE Pro render of this hard
    #    image. Pro bills whether the frame is upright or not, so we treat the fallback as USED
    #    the moment the call returns (an exception means nothing was billed). We RETURN this flag
    #    (used_pro) instead of bumping a module global — composes now run in parallel threads, and
    #    a shared global counter would race; the caller adds it up on the main thread.
    try:
        print("    non-pro gave no usable image; falling back to Nano Banana PRO for this one ...")
        ok = _compose_once(SCENE_EDIT_MODEL_PRO,
                           prompt + " CRITICAL: upright vertical frame.",
                           {"resolution": "2K"})
        used_pro = True                      # Pro billed the moment the call returned
        if ok:
            print("    Pro fallback produced the scene image.")
            return True, used_pro
        return os.path.exists(out_path), used_pro
    except Exception as e:
        print(f"    Pro fallback also failed ({e})")
    return os.path.exists(out_path), False


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


def _short_err(e) -> str:
    """One short line from a fal error (never the whole echoed request/prompt)."""
    s = str(e)
    low = s.lower()
    if "content_policy" in low or "content checker" in low or "flagged" in low:
        return "blocked by Seedance's content filter (wording too explicit)"
    return s[:140]


def _compose_scene(plan: dict) -> dict:
    """Compose ONE leader scene's image (Pass 2). Runs in a WORKER THREAD (many at once): it only
    writes this scene's own image file and returns (ok, used_pro) — it mutates NO shared counter,
    so the main thread folds the results in with no race. Wrapped so one bad compose skips that
    scene instead of crashing the whole compose wave."""
    c = plan["compose"]
    try:
        ok, used_pro = compose_scene_image(
            c["portraits"], c["sc_setting"], c["compose_shot"], c["action"],
            plan["start_img"], room_ref=c["room_ref"], people=c["locks"])
        return {"plan": plan, "ok": ok, "used_pro": used_pro}
    except Exception as e:
        print(f"    [{plan['i']}] compose error ({_short_err(e)})")
        return {"plan": plan, "ok": False, "used_pro": False}


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
    # compose_scene_image returns (ok, used_pro); coverage is disabled, so we only need ok here.
    ok, _ = compose_scene_image([portraits[n] for n in names], setting, dirn, "", wide,
                                room_ref=room_ref)
    if ok:
        angles["wide"] = wide
        n_new += 1

    base = angles.get("wide") or room_ref      # match the singles to the wide
    for k, n in enumerate(names):
        side = "right" if k == 0 else "left"   # keep each person's 180-rule eyeline
        sp = f"{out_prefix}_cu_{slug(n)}.png"
        shot = (f"tight vertical close-up of {descriptor(chars_by_name[n])}, head and "
                f"shoulders, looking to the {side} toward the other person just "
                f"off-camera, shallow depth of field")
        ok, _ = compose_scene_image([portraits[n]], setting, shot, "", sp, room_ref=base)
        if ok:
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
