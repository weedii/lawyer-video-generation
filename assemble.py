"""STAGE 2 - STEP 4: The EDITOR — assemble the final microdrama.

This is where the separately-generated clips become one smooth short film. The
research was blunt: what makes AI drama look pro is NOT the clips, it's the
connective tissue BETWEEN them. So this step does real editing craft:

  1. Per BEAT (one clip = one beat, e.g. one spoken line): trim the dead
     "staring" lead-in Veo puts on every clip (adaptive speech-onset), so there
     are no dead pauses before someone talks.
  2. Give every clip ONE colour look (a gentle shared grade, + an optional LUT)
     so clips generated independently stop looking like different cameras.
  3. Lay a CONTINUOUS ambient bed (room tone) per location + optional music UNDER
     the whole thing, so the audio never drops to silence at a cut — the single
     biggest trick for hiding the seams. The picture cuts; the sound does not.
  4. Join the picture with a gentle breathing zoom and tiny edge fades.

Quality first: we render at the biggest clip's size (no downscaling) at near-
lossless quality.

Usage:
    python assemble.py

Reads:  output/analysis.json   (each scene has an ordered "beats" list + "ambient";
                                falls back to the older single "clip" per scene)
        output/music.mp3        (OPTIONAL background music; used if present)
        output/look.cube        (OPTIONAL creative colour LUT; used if present)
Output: output/final_video.mp4
Cost:   free (runs locally with ffmpeg).

NOTE: an earlier assembly approach (cut ALL dead air incl. between glued lines,
then join with cross-dissolves) is preserved as a commented block at the very
BOTTOM of this file — we may want those pieces again, so they're kept, not lost.
"""
import os
import re
import sys
import json
import subprocess

OUT_DIR = "output"
FINAL = os.path.join(OUT_DIR, "final_video.mp4")
MUSIC = os.path.join(OUT_DIR, "music.mp3")
LOOK_LUT = os.path.join(OUT_DIR, "look.cube")   # optional creative grade (a .cube LUT)

OPEN_FADE = 0.3      # fade-in from black at the very start
CLOSE_FADE = 0.4     # fade-out to black at the very end
EDGE_FADE = 0.04     # tiny audio fade at each cut, to kill the pop/click
CRF = 16             # x264 quality: 16 is visually near-lossless (lower = better)
FPS = 25             # match the talking-model output (Veo/Kling are 25fps)
ZOOM_AMOUNT = 0.06   # gentle 6% zoom over a clip; alternates in/out per clip
SUPER_SCALE = 2      # render the zoom on a 2x frame so it stays smooth + sharp

# --- Colour grade -----------------------------------------------------------
# Independently-generated clips come out with slightly different colour/contrast,
# so intercut they read as "different cameras". We push every clip through ONE
# gentle grade so they share a look. This does NOT fix exposure differences (true
# hero-matching needs scopes/Resolve) — it imposes one consistent contrast + a
# faint filmic cast, enough to stop the clip-to-clip flicker. If output/look.cube
# exists we also apply that creative LUT on top for a stronger, reusable brand look.
BASE_GRADE = "eq=contrast=1.06:saturation=1.05:gamma=0.98"

# --- Continuous sound bed ---------------------------------------------------
# The ambient bed (room tone) and music run UNBROKEN under the whole video, mixed
# low, so the soundtrack never cuts even though the picture cuts every few seconds.
BED_VOL = 0.10       # ambient room-tone level under the dialogue (felt, not heard)
MUSIC_VOL = 0.12     # background music level under the dialogue

# Silent beats (reaction cutaways, establishing shots) are held only briefly — a
# reaction is a glance, not a scene. Veo's shortest clip is 4s, so we cap silent
# beats here so a cutaway doesn't overstay and stall the pace.
SILENT_MAX = 2.2     # max seconds to hold a silent (reaction/establishing) beat

# --- Dead lead-in trim ------------------------------------------------------
# Veo starts every clip with the character just LOOKING at the camera for 1-6s
# (silent, or only breathing / a shoe scuff) before actually speaking. We cut
# that off by finding where real SPEECH starts.
#
# We can't use plain loudness: a loud breath or shoe scuff is as loud as quiet
# speech. The reliable tell is that SPEECH lives in the voice band (300-3400Hz)
# and is SUSTAINED, while breath/shoe are broadband thumps and brief. So we:
#   1. band-pass to the voice range, then measure energy in 0.2s windows;
#   2. learn THIS clip's own quiet floor (20th percentile) and speech peak;
#   3. call it speech-onset at the first window above floor+margin that stays up
#      for ~0.8s (sustained) — a single breath/scuff spike can't trigger it;
#   4. trim to just before that (a small pad keeps the first word safe).
# Clips that open talking, or have no clear speech-vs-quiet gap (e.g. a silent
# reaction beat), are left untouched.
TRIM_HP = 300            # voice-band low edge (Hz)
TRIM_LP = 3400           # voice-band high edge (Hz)
TRIM_WIN = 0.2           # energy measured in windows this long (s)
TRIM_FLOOR_PCTL = 0.2    # the clip's quiet floor = this percentile of window energies
TRIM_MARGIN = 7.0        # speech must be this many dB above the floor
TRIM_MIN_RANGE = 8.0     # if peak-floor is smaller than this, no clear speech -> don't trim
TRIM_SUSTAIN_WIN = 4     # look at this many windows (4 x 0.2s = 0.8s) ...
TRIM_SUSTAIN_NEED = 3    # ... and require this many above threshold = "sustained"
TRIM_OPEN_T = 0.4        # if speech starts within this, the clip opens talking -> don't trim
TRIM_PAD = 0.25          # keep this much before the first word (safety)
TRIM_MAX_FRAC = 0.6      # never cut more than this fraction of a clip (runaway guard)
# After the SUSTAINED speech, a SHORT first word (e.g. "No.") can sit just before
# it, separated by a brief pause. Walk back to include it so it isn't chopped —
# only across a SMALL pause (a real first word stays close to the rest).
TRIM_BACK_GAP = 2        # allow this many quiet windows (x0.2s) between first word and main speech
TRIM_BACK_MAX = 1.2      # never reach back more than this many seconds (safety)


def run(cmd: list):
    """Run an ffmpeg command and stop on error."""
    subprocess.run(cmd, check=True, capture_output=True)


def audio_duration(path: str) -> float:
    """Return the length (seconds) of an audio (or video) file."""
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def voice_energy_track(path: str) -> list:
    """Voice-band energy of a clip in TRIM_WIN-second windows, as (time, dB).

    Band-passes to the speech range, then reads astats' per-window RMS level.
    Used to find where speech starts (see speech_onset)."""
    n = int(44100 * TRIM_WIN)
    af = (f"aresample=44100,highpass=f={TRIM_HP},lowpass=f={TRIM_LP},"
          f"asetnsamples=n={n},astats=metadata=1:reset=1,"
          f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", af, "-f", "null", "-"],
        capture_output=True, text=True).stderr
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", out)]
    rms = [float(x) for x in re.findall(r"RMS_level=(-?[\d.]+)", out)]
    m = min(len(times), len(rms))
    return list(zip(times[:m], rms[:m]))


def speech_onset(path: str) -> float:
    """Seconds of dead lead-in to cut from the FRONT of a clip.

    Finds the first SUSTAINED voice-band speech, learning this clip's own quiet
    floor so it works whether the lead-in is silent or full of breath/shoe noise.
    Returns 0 (no trim) for clips that open talking or have no clear
    speech-vs-quiet gap (e.g. a silent reaction beat). See the TRIM_* constants."""
    track = voice_energy_track(path)
    if len(track) < 5:
        return 0.0
    vals = sorted(v for _, v in track)
    floor = vals[int(len(vals) * TRIM_FLOOR_PCTL)]
    peak = vals[-1]
    if peak - floor < TRIM_MIN_RANGE:        # no clear speech vs quiet -> leave alone
        return 0.0
    thr = floor + TRIM_MARGIN
    for i, (t, v) in enumerate(track):
        if v < thr:
            continue
        window = [x[1] for x in track[i:i + TRIM_SUSTAIN_WIN]]
        if sum(1 for x in window if x >= thr) >= TRIM_SUSTAIN_NEED:
            # Found sustained speech at index i. Walk BACK to catch a short first
            # word sitting just before it (across only a brief pause), so we cut
            # to the true start of talking and never chop that first word.
            onset_i = i
            j, gap = i - 1, 0
            while j >= 0 and (track[i][0] - track[j][0]) <= TRIM_BACK_MAX:
                if track[j][1] >= thr:        # an earlier speech window = first word
                    onset_i, gap = j, 0
                else:
                    gap += 1
                    if gap > TRIM_BACK_GAP:   # too much quiet before -> real start found
                        break
                j -= 1
            t0 = track[onset_i][0]
            if t0 <= TRIM_OPEN_T:             # opens talking -> don't trim
                return 0.0
            total = audio_duration(path)
            return max(0.0, min(t0 - TRIM_PAD, total * TRIM_MAX_FRAC))
    return 0.0


def video_dims(path: str) -> tuple:
    """Return (width, height) of a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def grade_chain() -> str:
    """The colour-grade filter every clip gets: the gentle shared look, plus the
    optional creative LUT if output/look.cube is present."""
    chain = BASE_GRADE
    if os.path.exists(LOOK_LUT):
        chain += f",lut3d=file='{LOOK_LUT}'"
    return chain


def normalise(in_path: str, out_path: str, dur: float, w: int, h: int,
              zoom_in: bool = True, is_first: bool = False, is_last: bool = False,
              start: float = 0.0):
    """Re-encode one beat to the common size/fps/quality so the clips join cleanly
    WITHOUT losing quality, applying the shared colour grade and a gentle zoom.
    - High quality (crf 16) so re-encoding barely touches the picture.
    - The colour grade (grade_chain) unifies the look across independently-made clips.
    - -ac 2 forces stereo on every clip (some clips are mono; mixing layouts loses
      audio on some segments).
    dur: cut to this length; start: seek past the dead lead-in first."""
    # Gentle zoom across the clip, driven by TIME (t), not zoompan. zoompan is
    # built for stills and DROPS trailing frames on a video (it made every clip's
    # picture end ~0.5-1.3s before its audio). A time-driven scale is frame-exact:
    # every input frame is kept, so video length always equals audio length —
    # which matters for lip-sync and for the concat. zoom_in grows 1.00 -> 1.06.
    if zoom_in:
        Z = f"(1+{ZOOM_AMOUNT}*t/{dur:.3f})"
    else:
        Z = f"({1 + ZOOM_AMOUNT}-{ZOOM_AMOUNT}*t/{dur:.3f})"
    sw, sh = w * SUPER_SCALE, h * SUPER_SCALE

    # Supersample 2x, scale UP by the animated factor Z (scale supports a per-frame
    # `t` via eval=frame), centre-crop back, then apply the shared colour grade.
    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"scale=w='ceil({sw}*{Z}/2)*2':h='ceil({sh}*{Z}/2)*2':eval=frame,"
        f"crop={sw}:{sh},scale={w}:{h},{grade_chain()},fps={FPS},setpts=PTS-STARTPTS"
    )
    if is_first:
        vf += f",fade=t=in:st=0:d={OPEN_FADE}"
    if is_last:
        vf += f",fade=t=out:st={max(dur - CLOSE_FADE, 0):.3f}:d={CLOSE_FADE}"

    # Tiny audio fade at each edge kills the click at a hard cut; the continuous
    # sound bed (mixed in later) fills the little dip so it isn't heard.
    a_in = OPEN_FADE if is_first else EDGE_FADE
    a_out = CLOSE_FADE if is_last else EDGE_FADE
    af = (f"afade=t=in:st=0:d={a_in},"
          f"afade=t=out:st={max(dur - a_out, 0):.3f}:d={a_out}")

    # -ss BEFORE -i seeks past the dead lead-in; with re-encoding it is frame-
    # accurate. Both audio and video are seeked together, so lip-sync is preserved.
    run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", in_path,
        "-t", f"{dur:.3f}",
        "-vf", vf, "-af", af,
        "-r", str(FPS),
        "-c:v", "libx264", "-crf", str(CRF), "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])


def collect_beats(scenes: list) -> list:
    """Flatten every scene's ordered beats into one list, in play order. Each entry
    carries its scene index, kind, silence flag, and that scene's ambient bed.
    Falls back to the older single-clip format (scene["clip"]) so videos made
    before the beats change still assemble."""
    beats = []
    for i, sc in enumerate(scenes):
        sc_beats = sc.get("beats")
        if not sc_beats and sc.get("clip"):        # backward-compat: one clip = one beat
            sc_beats = [{"file": sc["clip"], "kind": sc.get("type", "line"),
                         "silent": False}]
        for b in (sc_beats or []):
            p = os.path.join(OUT_DIR, b.get("file", ""))
            if b.get("file") and os.path.exists(p):
                beats.append({
                    "path": p, "scene_i": i, "kind": b.get("kind", "line"),
                    "silent": bool(b.get("silent", False)),
                    "ambient": sc.get("ambient"),
                })
    return beats


def build_ambient_bed(prepared: list, out_path: str) -> str:
    """Build ONE continuous ambient track spanning the whole video: for each run of
    beats in the same scene, loop that scene's ambient bed to the run's length,
    then concat the segments gap-free. A scene with no bed of its own borrows the
    first available bed, so the room tone is truly unbroken. Returns out_path, or
    "" if no ambient bed exists at all.

    prepared: the normalised beats, each {"dur", "ambient"} in play order."""
    fallback = next((b["ambient"] for b in prepared if b.get("ambient")), None)
    if not fallback:
        return ""      # no ambient beds were generated -> no bed

    # Group consecutive beats into contiguous runs that share a bed, summing length.
    runs = []          # list of [ambient_file, total_seconds]
    for b in prepared:
        amb = b.get("ambient") or fallback
        if runs and runs[-1][0] == amb:
            runs[-1][1] += b["dur"]
        else:
            runs.append([amb, b["dur"]])

    seg_paths = []
    for k, (amb, length) in enumerate(runs):
        seg = os.path.join(OUT_DIR, f"_bedseg_{k:02d}.m4a")
        # -stream_loop -1 repeats the short loop; -t caps it to this run's length.
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", os.path.join(OUT_DIR, amb),
             "-t", f"{length:.3f}", "-ar", "44100", "-ac", "2",
             "-c:a", "aac", "-b:a", "128k", seg])
        seg_paths.append(seg)

    listf = out_path + ".txt"
    with open(listf, "w") as f:
        for p in seg_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
         "-c", "copy", out_path])
    for p in seg_paths + [listf]:
        if os.path.exists(p):
            os.remove(p)
    return out_path


def mix_final(joined: str, bed: str):
    """Mix the continuous ambient bed + optional music UNDER the joined dialogue and
    write the final video. Because bed + music never cut, the soundtrack stays
    continuous even though the picture cuts. The dialogue stays at full level
    (normalize=0); the beds sit low underneath."""
    extra = []
    if bed:
        extra.append((bed, BED_VOL))
    if os.path.exists(MUSIC):
        extra.append((MUSIC, MUSIC_VOL))
    if not extra:                              # nothing to lay under -> keep the plain join
        os.replace(joined, FINAL)
        print("  (no ambient bed or music — kept the plain join)")
        return

    inputs = ["-i", joined]
    for path, _ in extra:
        inputs += ["-i", path]

    # Turn each bed down, then amix everything with the dialogue. duration=first
    # stops at the video length; normalize=0 keeps the dialogue loud instead of
    # dividing every input down.
    parts, labels = [], ["[0:a]"]
    for idx, (_, vol) in enumerate(extra, start=1):
        parts.append(f"[{idx}:a]volume={vol}[b{idx}]")
        labels.append(f"[b{idx}]")
    fc = (";".join(parts) + ";" + "".join(labels) +
          f"amix=inputs={len(labels)}:duration=first:normalize=0[a]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", FINAL])
    laid = " + ".join(["ambient"] * bool(bed) + ["music"] * os.path.exists(MUSIC))
    print(f"  laid continuous sound bed under the cuts ({laid})")


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    scenes = (data.get("script") or {}).get("scenes", [])
    beats = collect_beats(scenes)
    if not beats:
        sys.exit("No scene beats found. Run scene_clips.py first.")

    # Pick the target size = the BIGGEST beat, so we never downscale good clips.
    dims = [video_dims(b["path"]) for b in beats]
    target_w, target_h = max(dims, key=lambda wh: wh[0] * wh[1])
    print(f"Editing {len(beats)} beats at {target_w}x{target_h} ...")

    # 1) Prepare each beat: trim the dead lead-in, grade, zoom, size — one at a time.
    prepared = []
    for k, b in enumerate(beats, 1):
        out_path = os.path.join(OUT_DIR, f"_norm_{k:02d}.mp4")
        full = audio_duration(b["path"])
        if b["silent"]:
            # A reaction/establishing cutaway: no speech to find — just hold it briefly.
            start, dur = 0.0, min(full, SILENT_MAX)
        else:
            # A narration beat is a lone speaker who often pauses before the first
            # word, so we still trim that staring lead-in. A whole-scene DIALOGUE clip
            # opens already mid-exchange (the first speaker talks from the very top),
            # so searching for a speech onset here would slice off that first line —
            # keep it whole.
            start = 0.0 if b["kind"] == "dialogue" else speech_onset(b["path"])
            dur = full - start
        zoom_in = (k % 2 == 1)                  # alternate so the video breathes
        normalise(b["path"], out_path, dur, target_w, target_h, zoom_in=zoom_in,
                  is_first=(k == 1), is_last=(k == len(beats)), start=start)
        prepared.append({"path": out_path, "dur": dur, "ambient": b["ambient"]})
        trim_note = f", cut {start:.2f}s lead-in" if start > 0.05 else ""
        print(f"  [{k}] {os.path.basename(b['path'])} -> {dur:.2f}s "
              f"({'zoom in' if zoom_in else 'zoom out'}{trim_note})")

    # 2) Join the picture + its aligned dialogue (hard cuts; the beds smooth them).
    list_file = os.path.join(OUT_DIR, "_concat.txt")
    with open(list_file, "w") as f:
        for b in prepared:
            f.write(f"file '{os.path.basename(b['path'])}'\n")
    joined = os.path.join(OUT_DIR, "_joined.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", joined])
    print("  joined all beats")

    # 3) Build ONE continuous ambient bed spanning the whole video (per location).
    bed = build_ambient_bed(prepared, os.path.join(OUT_DIR, "_bed.m4a"))

    # 4) Mix the ambient bed + optional music UNDER the dialogue -> final video.
    mix_final(joined, bed)

    # 5) Clean up the temporary pieces.
    for k in range(1, len(prepared) + 1):
        p = os.path.join(OUT_DIR, f"_norm_{k:02d}.mp4")
        if os.path.exists(p):
            os.remove(p)
    for p in (list_file, joined, os.path.join(OUT_DIR, "_bed.m4a")):
        if os.path.exists(p):
            os.remove(p)

    print(f"\nDONE -> {FINAL}")
    print("Watch it: open output/final_video.mp4")


if __name__ == "__main__":
    main()


# ============================================================================
# PARKED FOR FUTURE REUSE (kept, not deleted) — the earlier assembly approach:
# cut ALL dead air (front + BETWEEN glued lines + tail) from each SCENE clip, then
# join scenes with a cross-DISSOLVE. Superseded by the per-beat editor above (each
# line is now its own beat, so there is no "between lines" gap inside a clip to
# remove, and we hard-cut + lay a continuous bed instead of dissolving). If we ever
# want tail-trim, gap removal, or dissolves back, lift these straight from here.
#
# # --- extra constants ---
# TRIM_TAIL_PAD = 0.35     # keep this much AFTER the last word (never clip it)
# TRIM_TAIL_MIN = 0.30     # only bother tail-trimming if it removes at least this much
# DEAD_GAP_MAX = 0.45      # a silence longer than this is a Veo reset -> cut it out
# DEAD_KEEP_PAD = 0.18     # keep this much speech-silence around each kept region
# DEAD_MIN_REGION = 0.35   # ignore speech blips shorter than this (a breath/scuff)
# DEAD_MIN_CUT = 0.40      # only re-encode a clip if we'd remove at least this much
# TRANSITION = 0.3         # cross-dissolve length between clips (soft cut)
#
# def video_duration(path: str) -> float:
#     """Length (s) of the VIDEO stream — what xfade offsets must be based on (the
#     format/audio duration can be a hair longer and would drift the dissolves)."""
#     out = subprocess.run(
#         ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
#          "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
#         capture_output=True, text=True).stdout.strip()
#     try:
#         return float(out)
#     except ValueError:
#         return audio_duration(path)
#
# def speech_end(path: str, total: float) -> float:
#     """Time (s) where real speech ENDS, so we can cut Veo's silent tail-reset.
#     Mirror of speech_onset from the other end: find the LAST sustained voice-band
#     window, walk forward to catch a short final word after a brief pause, then keep
#     TRIM_TAIL_PAD so the last word is never clipped. Returns `total` (no tail trim)
#     for all-talk clips or when there's too little tail to bother."""
#     track = voice_energy_track(path)
#     if len(track) < 5:
#         return total
#     vals = sorted(v for _, v in track)
#     floor = vals[int(len(vals) * TRIM_FLOOR_PCTL)]
#     peak = vals[-1]
#     if peak - floor < TRIM_MIN_RANGE:
#         return total
#     thr = floor + TRIM_MARGIN
#     last = None
#     for i in range(len(track) - 1, -1, -1):
#         if track[i][1] < thr:
#             continue
#         window = [x[1] for x in track[max(0, i - TRIM_SUSTAIN_WIN + 1):i + 1]]
#         if sum(1 for x in window if x >= thr) >= TRIM_SUSTAIN_NEED:
#             last = i
#             break
#     if last is None:
#         return total
#     end_i = last
#     k, gap = last + 1, 0
#     while k < len(track) and (track[k][0] - track[last][0]) <= TRIM_BACK_MAX:
#         if track[k][1] >= thr:
#             end_i, gap = k, 0
#         else:
#             gap += 1
#             if gap > TRIM_BACK_GAP:
#                 break
#         k += 1
#     t_end = track[end_i][0] + TRIM_WIN + TRIM_TAIL_PAD
#     if total - t_end < TRIM_TAIL_MIN:
#         return total
#     return min(total, t_end)
#
# def _speech_regions(track: list, thr: float) -> list:
#     """Merge windows into speech regions: runs of above-threshold windows, tiny
#     blips dropped and regions across only a SHORT gap joined."""
#     regions = []
#     i, n = 0, len(track)
#     while i < n:
#         if track[i][1] >= thr:
#             j = i
#             while j < n and track[j][1] >= thr:
#                 j += 1
#             regions.append([track[i][0], track[j - 1][0] + TRIM_WIN])
#             i = j
#         else:
#             i += 1
#     regions = [r for r in regions if r[1] - r[0] >= DEAD_MIN_REGION]
#     merged = []
#     for r in regions:
#         if merged and r[0] - merged[-1][1] <= DEAD_GAP_MAX:
#             merged[-1][1] = r[1]
#         else:
#             merged.append(r)
#     return merged
#
# def remove_dead_air(in_path: str, out_path: str) -> bool:
#     """Keep the speech (+ a small pad) and cut every long silent gap — front,
#     BETWEEN lines, and tail — so a multi-line scene has no dead stares. Returns
#     True and writes a tightened clip; returns False (leave as is) for all-talk
#     clips or when there's almost nothing to remove."""
#     total = audio_duration(in_path)
#     track = voice_energy_track(in_path)
#     if len(track) < 5:
#         return False
#     vals = sorted(v for _, v in track)
#     floor = vals[int(len(vals) * TRIM_FLOOR_PCTL)]
#     peak = vals[-1]
#     if peak - floor < TRIM_MIN_RANGE:
#         return False
#     thr = floor + TRIM_MARGIN
#     regions = _speech_regions(track, thr)
#     if not regions:
#         return False
#     segs = [[max(0.0, s - DEAD_KEEP_PAD), min(total, e + DEAD_KEEP_PAD)]
#             for s, e in regions]
#     kept = [segs[0]]
#     for s, e in segs[1:]:
#         if s <= kept[-1][1]:
#             kept[-1][1] = max(kept[-1][1], e)
#         else:
#             kept.append([s, e])
#     if total - sum(e - s for s, e in kept) < DEAD_MIN_CUT:
#         return False
#     sel = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in kept)
#     vf = f"select='{sel}',setpts=N/FRAME_RATE/TB"
#     af = f"aselect='{sel}',asetpts=N/SR/TB"
#     run(["ffmpeg", "-y", "-i", in_path, "-vf", vf, "-af", af,
#          "-c:v", "libx264", "-crf", str(CRF), "-preset", "medium", "-pix_fmt", "yuv420p",
#          "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", out_path])
#     return os.path.exists(out_path)
#
# # --- cross-dissolve JOIN (was step 2 of main) ---
# # Join normalised clips with a short cross-dissolve at every cut (xfade blends the
# # picture, acrossfade blends the audio the same way so they stay in sync). durs[]
# # is each clip's video_duration(); offsets accumulate the running length minus the
# # overlaps already spent.
# #   D = TRANSITION
# #   vparts, aparts = [], []
# #   vlabel, alabel = "0:v", "0:a"
# #   total = durs[0]
# #   for k in range(1, len(normalised)):
# #       off = total - D
# #       vparts.append(f"[{vlabel}][{k}:v]xfade=transition=fade:duration={D}:offset={off:.3f}[vx{k}]")
# #       aparts.append(f"[{alabel}][{k}:a]acrossfade=d={D}[ax{k}]")
# #       vlabel, alabel = f"vx{k}", f"ax{k}"
# #       total = total + durs[k] - D
# #   fc = ";".join(vparts + aparts)
# #   run(["ffmpeg", "-y", *inputs, "-filter_complex", fc,
# #        "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
# #        "-c:v", "libx264", "-crf", str(CRF), "-preset", "medium",
# #        "-pix_fmt", "yuv420p", "-r", str(FPS),
# #        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", joined])
# ============================================================================
