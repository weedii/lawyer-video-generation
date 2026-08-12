"""Build every scene — the STEP that drives image + video (memoir VOICEOVER pipeline).

Each scene is made in three passes, all here in main(): PLAN (work out, per scene, whether to
reuse or compose an image, and in what order), COMPOSE the images IN PARALLEL (scene_image.py,
two waves so a repeated room copies its first image), then RENDER the clips IN PARALLEL
(scene_video.py). The picture and the movement used to be one big file; they were split so each
half is easy to find — this file is just the orchestrator that ties them together.

It also still holds the small ElevenLabs AUDIO helpers (tts / make_ambient / make_music), which
audio_maker.py imports from here to make the voiceover, ambience beds and music in the audio step.

Usage:
    python scene_clips.py     (run audio_maker.py first, so the voiceovers exist)

Reads:  output/analysis.json + output/vo_XX.mp3
Output: output/clip_XX.mp4 (one per scene) + each scene's ordered scene["beats"] list.
Cost:   Seedance $0.026/s (video) + Nano Banana 2 $0.08/image. Audio cost lives in audio_maker.
"""
import os
import sys
import json
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import costs
# The two halves of the scene step: the image compositor and the clip renderer.
# OUT_DIR / slug / audio_duration are also RE-EXPORTED from here — audio_maker.py imports them
# (and tts / make_ambient / make_music, defined below) from scene_clips, so they must stay in
# this module's namespace even though main() itself doesn't call slug/OUT_DIR directly.
from scene_image import (OUT_DIR, slug, audio_duration, identity_lock, sheet_ref, group_shot,
                         compose_coverage, _compose_scene, COVERAGE)
from scene_video import (_render_task, fit_duration, billed_seconds, scene_duration,
                         build_scene_prompt, build_narration_vo_prompt, make_detail_insert,
                         DETAIL_INSERTS)

load_dotenv()
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVEN_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty (needed for the narrator voiceover).")
# (compose_scene_image RETURNS whether it fell back to Pro; the caller sums those up on the main
# thread. We don't keep a module-level counter because composes now run in parallel threads and a
# shared global would race.)
# The narrator VOICEOVER: we make the words ourselves with ElevenLabs text-to-speech and lay
# them OVER the silent clip (no lip-sync — the characters are never heard). This is the only
# voice in the video.
TTS_MODEL = "eleven_multilingual_v2"                # ElevenLabs text-to-speech (the voiceover)
# Voice settings tuned for CLARITY — the narration is the ONLY voice in the video and it's
# watched while scrolling, so it must be easy to catch. Steady (higher stability) so words
# don't slur, and a touch slower (speed 0.95) so each line lands.
VOICE_SETTINGS = {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0, "speed": 0.95}

# How many Seedance clips to render AT THE SAME TIME. Each clip is an independent fal job
# (submit, then poll), so they can all run in parallel instead of one after another — the old
# serial loop is why a 9-scene video's render step took ~23 minutes; in parallel it takes about
# as long as the single slowest clip (~2-3 min). The default is 9 because a video is 7-9 scenes,
# so every clip renders in ONE wave (a cap of 6 left 2 clips waiting for a second round, which
# roughly doubled the render time). Lower it to 1 (fully serial) if fal ever rate-limits.
# Override with the MAX_PARALLEL_RENDERS env var without touching the code.
MAX_PARALLEL_RENDERS = int(os.getenv("MAX_PARALLEL_RENDERS", "9"))

# DRAFT mode (run with DRAFT=1 in the environment) renders cheap for iteration: the shortest
# clip length and no voiceover, so you can check framing, identity and motion without paying
# for full-length clips or the TTS. A real run leaves it off, so each scene gets its full-length
# silent clip with the narrator's voiceover laid over it.
DRAFT = os.getenv("DRAFT") == "1"


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
    scene_pro = 0               # of those, how many fell back to Nano Banana PRO this run
    reused_video_seconds = 0.0  # Seedance seconds of clips reused from a prior run (paid before)
    reused_images = 0           # composed images reused from a prior run (paid before)
    reused_pro = 0              # of those reused images, how many were Pro-fallback composes
    # This step runs in three passes: (1) a serial PLAN pass works out, per scene, what it needs
    # (reuse an old clip, reuse an image, or compose a new one) and in which order the composes
    # must run; (2) the scene images are COMPOSED in parallel — in two waves so a repeated room's
    # anchor is ready before scenes that copy it (see the plan pass); (3) the clips are RENDERED
    # in parallel. Keeping the composes parallel (not one-at-a-time) is what this refactor adds.
    render_tasks = []       # scenes to render, filled in the queue pass after the composes finish
    plans = []              # per-scene plan built by the PLAN pass and consumed by the later passes
    location_ref = {}       # setting -> first image in that place (the room anchor others copy)
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

    # ---------- PASS 1: PLAN every scene (serial, no model calls) ----------
    # Walk the scenes IN ORDER and decide what each one needs, without calling any model yet.
    # This is where the room-anchor ORDER is worked out: the first scene in a location has no room
    # to copy (wave "A"), and later scenes in the SAME location copy that first image (wave "B") so
    # the room stays identical. A scene whose clip is already on disk is reused whole; a scene that
    # repeats an exact cast+place reuses that image; everything else is a leader to be composed.
    for i, sc in enumerate(scenes, 1):
        clip_name = f"clip_{i:02d}.mp4"
        clip_path = os.path.join(OUT_DIR, clip_name)
        sc_setting = sc.get("setting", setting) or setting
        loc_key = sc_setting.strip().lower()
        is_narr = sc.get("type") == "narration"

        # Clip already on disk -> reuse the whole scene untouched (no compose, no render), ONE
        # clean line. Its clip seconds + own image still count toward the video's TRUE total.
        # (Detail inserts are off; if they're ever re-enabled, a reused clip's insert beat would
        # need rebuilding here — noted so it isn't forgotten.)
        if os.path.exists(clip_path):
            who = ((sc.get("characters") or [lead_name or "?"])[0] if is_narr
                   else " + ".join(sc.get("characters", [])) or "?")
            sc["beats"] = [{"file": clip_name, "kind": sc.get("type", "dialogue"),
                            "speaker": lead_name if is_narr else None, "silent": False}]
            reused_video_seconds += billed_seconds(sc)
            # Count the scene's OWN composed image only if it has its own file (a scene that shared
            # another's image has none), else we'd double-count one paid image.
            own_img = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
            if os.path.exists(own_img):
                reused_images += 1
                reused_pro += sc.get("image_pro", 0)
                # Keep the room anchor so a scene re-rendered this run in the same location still
                # matches its untouched neighbours (without it the room can drift).
                location_ref.setdefault(loc_key, own_img)
            print(f"  [{i}] {sc.get('type','scene').upper()} [{who}] -> {clip_name} "
                  f"(reused from last run, $0)")
            continue

        # Who is IN this shot (only characters that have a locked portrait).
        if is_narr:
            names = [n for n in sc.get("characters", [])
                     if chars_by_name.get(n, {}).get("file")]
            if not names and main_names:
                names = [main_names[0]]
            if not names:
                print(f"  [{i}] NARRATION skipped: no lead portrait.")
                continue
            names = names[:1]
        else:
            # EVERYONE the scene puts in the room (speakers + silent people present), so every
            # visible face comes from a locked portrait; a named person missing from the composite
            # is what makes the model invent a random face. Anyone without a portrait is dropped.
            names = [n for n in (sc.get("onscreen") or sc.get("characters", []))
                     if chars_by_name.get(n, {}).get("file")]
            if not names:
                print(f"  [{i}] DIALOGUE skipped: no character images.")
                continue
        # A single cropped FRONT view per character as the compose reference (see sheet_ref).
        portraits = [sheet_ref(os.path.join(OUT_DIR, chars_by_name[n]["file"]), n)
                     for n in names]
        cache_key = (frozenset(names), loc_key)

        # Decide this scene's image: reuse an identical cast+place image, reuse one from a prior
        # run on disk, or COMPOSE a new one. compose = None means nothing to compose; otherwise it
        # holds everything the parallel compose worker needs plus its wave (A = first in this room,
        # B = copies this room's first image).
        start_img = os.path.join(OUT_DIR, f"scene_{i:02d}.png")
        compose = None
        if COVERAGE and not is_narr and names:
            # Coverage (shot/reverse-shot) is DISABLED; if switched on it composes several angles.
            # It stays SERIAL here (rare, off) — it is not part of the parallel waves.
            cov = coverage_cache.get(cache_key)
            if cov and all(os.path.exists(p) for p in cov.values()):
                print(f"  [{i}] (reusing coverage — same people, same place, $0 saved)")
                start_img = cov["wide"]
            else:
                cov, n_new = compose_coverage(names, chars_by_name, sc_setting,
                                              location_ref.get(loc_key),
                                              os.path.join(OUT_DIR, f"cov_{i:02d}"))
                scene_images += n_new
                if cov.get("wide"):
                    start_img = cov["wide"]
                    location_ref.setdefault(loc_key, cov["wide"])
                    coverage_cache[cache_key] = cov
        elif cache_key in compose_cache:
            # Follower: the exact same cast in the exact same place was already composed by an
            # earlier (leader) scene -> reuse that image, compose nothing, pay nothing.
            start_img = compose_cache[cache_key]
            print(f"  [{i}] (reusing scene image — same people, same place, $0 saved)")
        elif os.path.exists(start_img):
            # Reused from a previous run's disk: $0 now, but still one image the video is built
            # from, so it counts toward the TRUE total (not toward spent).
            print(f"  [{i}] (reusing scene image {os.path.basename(start_img)} — already on disk, $0)")
            reused_images += 1
            reused_pro += sc.get("image_pro", 0)
            location_ref.setdefault(loc_key, start_img)
            compose_cache[cache_key] = start_img
        else:
            # Leader: a new image must be composed. Seat the cast (dialogue two-shot / narration
            # solo) and pin each person's identity + clothing colour via their lock sentence.
            compose_shot = (group_shot(names, chars_by_name, sc.get("shot", ""))
                            if not is_narr and len(names) >= 2 else sc.get("shot", ""))
            locks = [identity_lock(chars_by_name[n]) for n in names]
            # Wave A = first image in this location (no room reference); wave B = a repeat of a
            # location whose anchor image already exists, so it copies that room. Running ALL of
            # wave A before wave B guarantees a B scene's room_ref is on disk when it composes.
            if loc_key in location_ref:
                wave, room_ref = "B", location_ref[loc_key]
            else:
                wave, room_ref = "A", None
                location_ref[loc_key] = start_img   # this leader becomes the room's anchor
            compose_cache[cache_key] = start_img
            compose = {"wave": wave, "room_ref": room_ref, "portraits": portraits,
                       "sc_setting": sc_setting, "compose_shot": compose_shot,
                       "action": sc.get("action", ""), "locks": locks}

        # Everything else the render + its result line will need, captured now while we have the
        # scene's context. used_pro_fallback is filled by the compose pass (stays False if the
        # image was reused rather than composed).
        who = names[0] if is_narr else " + ".join(sc.get("characters", []))
        prompt = (build_narration_vo_prompt(sc, lead_c) if is_narr
                  else build_scene_prompt(sc, chars_by_name))
        if DRAFT:                                   # cheap preview: shortest clip, no voiceover
            vo_path, vo_secs, dur = "", 0.0, "5s"
        else:
            vo_file = sc.get("vo_file", "")
            vo_path = os.path.join(OUT_DIR, vo_file) if vo_file else ""
            vo_secs = sc.get("vo_seconds", 0.0) if vo_path and os.path.exists(vo_path) else 0.0
            # Size the clip to cover the voiceover (a touch longer so the VO is never clipped).
            dur = fit_duration(vo_secs) if vo_secs > 0 else scene_duration(sc.get("dialogue", []))
        plans.append({
            "i": i, "sc": sc, "clip_name": clip_name, "clip_path": clip_path,
            "start_img": start_img, "compose": compose, "loc_key": loc_key,
            "sc_setting": sc_setting, "is_narr": is_narr, "who": who,
            "prompt": prompt, "dur": dur, "vo_path": vo_path, "vo_secs": vo_secs,
            "type_label": sc.get("type", "scene").upper(), "used_pro_fallback": False,
        })

    # ---------- PASS 2: COMPOSE the scene images IN PARALLEL (two waves) ----------
    # Make the leader images concurrently instead of one at a time. Wave A (first image in each
    # location, no room reference) runs first and fully; then wave B (repeats of a location) runs,
    # each copying its location's now-finished anchor image. Within a wave every compose is
    # independent, so they all run at once, up to MAX_PARALLEL_RENDERS. Results are folded in on
    # the MAIN thread (scene_images / scene_pro / the scene's image_pro), so the workers never
    # touch shared state.
    wave_a = [p for p in plans if p["compose"] and p["compose"]["wave"] == "A"]
    wave_b = [p for p in plans if p["compose"] and p["compose"]["wave"] == "B"]
    if wave_a or wave_b:
        n_compose = len(wave_a) + len(wave_b)
        print(f"Composing {n_compose} scene image{'s' if n_compose != 1 else ''} in parallel "
              f"(up to {MAX_PARALLEL_RENDERS} at a time) ...")
        for wave in (wave_a, wave_b):
            if not wave:
                continue
            workers = min(MAX_PARALLEL_RENDERS, len(wave))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for fut in as_completed([pool.submit(_compose_scene, p) for p in wave]):
                    res = fut.result()
                    p = res["plan"]
                    if res["ok"]:
                        scene_images += 1
                        p["used_pro_fallback"] = res["used_pro"]
                        p["sc"]["image_pro"] = 1 if res["used_pro"] else 0
                        if res["used_pro"]:
                            scene_pro += 1
                    else:
                        p["compose_failed"] = True
                        print(f"  [{p['i']}] skipped: no scene image could be composed.")

    # ---------- PASS 3: QUEUE a render task per scene whose image is ready ----------
    # Now that the images exist, build the render tasks in scene order. A leader whose compose
    # failed, or any scene missing its image, is skipped. Detail inserts (off by default) are built
    # here, serially, because they need the composed image.
    for p in plans:
        if p.get("compose_failed"):
            continue
        start_img = p["start_img"]
        if not (start_img and os.path.exists(start_img)):
            print(f"  [{p['i']}] skipped: no scene image could be composed.")
            continue
        beat = {"file": p["clip_name"], "kind": p["sc"].get("type", "dialogue"),
                "speaker": lead_name if p["is_narr"] else None, "silent": False}
        # Detail insert (dialogue scenes only, DISABLED): open a new location on a silent,
        # face-free object shot. Serial + cached per location because it needs the room image.
        insert_beats = []
        if DETAIL_INSERTS and not p["is_narr"] and p["loc_key"] not in detail_by_loc:
            detail_by_loc[p["loc_key"]] = None
            ins, imgs, dsecs = make_detail_insert(
                p["i"], p["sc"].get("detail", ""), p["sc_setting"],
                location_ref.get(p["loc_key"]) or start_img)
            scene_images += imgs
            detail_video_seconds += dsecs
            if ins:
                detail_by_loc[p["loc_key"]] = ins
                insert_beats = [ins]
        render_tasks.append({
            "i": p["i"], "sc": p["sc"], "clip_name": p["clip_name"], "clip_path": p["clip_path"],
            "start_img": start_img, "prompt": p["prompt"], "dur": p["dur"],
            "vo_path": p["vo_path"], "vo_secs": p["vo_secs"],
            "beat": beat, "insert_beats": insert_beats, "who": p["who"],
            "used_pro_fallback": p["used_pro_fallback"],
            "type_label": p["type_label"],
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
    total_pro = scene_pro + reused_pro
    scene_cost = total_video_seconds * silent_rate
    detail_cost = detail_video_seconds * silent_rate
    image_cost = _image_price(total_images, total_pro)
    video_cost = scene_cost + detail_cost + image_cost

    # SPENT this run = only the pieces we regenerated now (reused ones cost $0 today).
    scene_spent = scene_video_seconds * silent_rate
    image_spent = _image_price(scene_images, scene_pro)

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
