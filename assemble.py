"""STAGE 2 - STEP 5: Assemble the final microdrama video.

It joins the clips, in script order, into one vertical video and (optionally)
adds background music. No captions.

Quality first: the scene clips can be high resolution (Kling scenes are ~9:16
HD). So we DON'T downscale them — we find the biggest clip size and render the
whole video at that size and frame rate, re-encoding at near-lossless quality.

Motion: a gentle zoom that alternates in/out per clip so the video breathes.
It is done on a 2x-SUPERSAMPLED frame at the clips' native size, so it stays
smooth (no jitter) AND sharp (no quality loss).

Clean joins (all FREE, ffmpeg):
  - a tiny audio fade at every clip edge, to remove the click/pop at a hard cut;
  - a fade-from-black at the start and fade-to-black at the end.

Usage:
    python assemble.py

Reads:  output/analysis.json   (script scenes, each with a "clip" file)
        output/music.mp3        (OPTIONAL background music; used if present)
Output: output/final_video.mp4
Cost:   free (runs locally with ffmpeg).
"""
import os
import re
import sys
import json
import subprocess

OUT_DIR = "output"
FINAL = os.path.join(OUT_DIR, "final_video.mp4")
MUSIC = os.path.join(OUT_DIR, "music.mp3")

OPEN_FADE = 0.3      # fade-in from black at the very start
CLOSE_FADE = 0.4     # fade-out to black at the very end
EDGE_FADE = 0.04     # tiny audio fade at each cut, to kill the pop/click
CRF = 16             # x264 quality: 16 is visually near-lossless (lower = better)
FPS = 25             # match the talking-model output (OmniHuman/Kling are 25fps)
ZOOM_AMOUNT = 0.06   # gentle 6% zoom over a clip; alternates in/out per clip
SUPER_SCALE = 2      # render the zoom on a 2x frame so it stays smooth + sharp

# --- Dead lead-in trim ------------------------------------------------------
# Kling starts every dialogue clip with the characters just LOOKING at the
# camera for 1-6s (silent, or only breathing / a shoe scuff) before they
# actually speak. We cut that off by finding where real SPEECH starts.
#
# We can't use plain loudness: a loud breath or shoe scuff is as loud as quiet
# speech. The reliable tell is that SPEECH lives in the voice band (300-3400Hz)
# and is SUSTAINED, while breath/shoe are broadband thumps and brief. So we:
#   1. band-pass to the voice range, then measure energy in 0.2s windows;
#   2. learn THIS clip's own quiet floor (20th percentile) and speech peak;
#   3. call it speech-onset at the first window that is above floor+margin AND
#      stays up for ~0.8s (sustained) — a single breath/scuff spike can't
#      trigger it;
#   4. trim to just before that (a small pad keeps the first word safe).
# Clips that open talking, or have no clear speech-vs-quiet gap (e.g. narration
# voiceover), are left untouched.
TRIM_HP = 300            # voice-band low edge (Hz)
TRIM_LP = 3400           # voice-band high edge (Hz)
TRIM_WIN = 0.2           # energy measured in windows this long (s)
TRIM_FLOOR_PCTL = 0.2    # the clip's quiet floor = this percentile of window energies
TRIM_MARGIN = 7.0        # speech must be this many dB above the floor
TRIM_MIN_RANGE = 8.0     # if peak-floor is smaller than this, there's no clear speech -> don't trim
TRIM_SUSTAIN_WIN = 4     # look at this many windows (4 x 0.2s = 0.8s) ...
TRIM_SUSTAIN_NEED = 3    # ... and require this many above threshold = "sustained"
TRIM_OPEN_T = 0.4        # if speech starts within this, the clip opens talking -> don't trim
TRIM_PAD = 0.25          # keep this much before the first word (safety)
TRIM_MAX_FRAC = 0.6      # never cut more than this fraction of a clip (runaway guard)
# After we find the SUSTAINED speech, a SHORT first word (e.g. "No.") can sit just
# before it, separated by a brief pause. We walk back to include it so it isn't
# chopped. Only across a SMALL pause (a real first word stays close to the rest;
# a longer gap means the earlier sound was junk we should keep cutting).
TRIM_BACK_GAP = 2        # allow this many quiet windows (x0.2s) between first word and main speech
TRIM_BACK_MAX = 1.2      # never reach back more than this many seconds (safety)


def run(cmd: list[str]):
    """Run an ffmpeg command and stop on error."""
    subprocess.run(cmd, check=True, capture_output=True)


def audio_duration(path: str) -> float:
    """Return the length (seconds) of an audio (or video) file."""
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def voice_energy_track(path: str) -> list[tuple[float, float]]:
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
    floor so it works whether the lead-in is silent or full of breath/shoe
    noise. Returns 0 (no trim) for clips that open talking or have no clear
    speech-vs-quiet gap. See the TRIM_* constants for the full method."""
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


def video_dims(path: str) -> tuple[int, int]:
    """Return (width, height) of a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def normalise(in_path: str, out_path: str, dur: float, w: int, h: int,
              zoom_in: bool = True, is_first: bool = False, is_last: bool = False,
              start: float = 0.0):
    """Re-encode one clip to the common size/fps/quality so the clips join
    cleanly WITHOUT losing quality, applying a gentle smooth zoom.
    - High quality (crf 16) so re-encoding barely touches the picture.
    - The zoom is rendered on a 2x frame (supersampled) so it is smooth and
      stays sharp, then output at the native WxH.
    - -ac 2 forces stereo on every clip (narrator clips are mono, dialogue
      stereo; mixing layouts loses audio on some segments).
    dur: cut to this length (only used when a clip is much longer than its
    speech, i.e. the Kling padding case)."""
    # Linear zoom across the clip (exact endpoints, even motion). zoom_in grows
    # 1.00 -> 1.06; otherwise it shrinks 1.06 -> 1.00. Alternating per clip makes
    # the video gently breathe in and out.
    frames = max(int(round(dur * FPS)) - 1, 1)
    if zoom_in:
        zexpr = f"1.0+{ZOOM_AMOUNT}*on/{frames}"
    else:
        zexpr = f"{1.0 + ZOOM_AMOUNT}-{ZOOM_AMOUNT}*on/{frames}"
    sw, sh = w * SUPER_SCALE, h * SUPER_SCALE

    # Upscale 2x (supersample), zoom on that big frame, output at native WxH.
    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"fps={FPS}:s={w}x{h}"
    )
    if is_first:
        vf += f",fade=t=in:st=0:d={OPEN_FADE}"
    if is_last:
        vf += f",fade=t=out:st={max(dur - CLOSE_FADE, 0):.3f}:d={CLOSE_FADE}"

    a_in = OPEN_FADE if is_first else EDGE_FADE
    a_out = CLOSE_FADE if is_last else EDGE_FADE
    af = (f"afade=t=in:st=0:d={a_in},"
          f"afade=t=out:st={max(dur - a_out, 0):.3f}:d={a_out}")

    # -ss BEFORE -i seeks past the dead lead-in; with re-encoding it is
    # frame-accurate. Both audio and video are seeked together, so lip-sync is
    # preserved. -t then bounds the (already trimmed) length.
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


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    scenes = (data.get("script") or {}).get("scenes", [])
    clips = [sc for sc in scenes if sc.get("clip")]
    if not clips:
        sys.exit("No scene clips found. Run scene_clips.py first.")

    # Pick the target size = the BIGGEST clip, so we never downscale good clips.
    paths = [os.path.join(OUT_DIR, sc["clip"]) for sc in clips]
    dims = [video_dims(p) for p in paths]
    target_w, target_h = max(dims, key=lambda wh: wh[0] * wh[1])
    print(f"Assembling {len(clips)} scenes at {target_w}x{target_h} ...")

    # 1) Normalise each clip to the common size/fps/quality (no zoom, no downscale).
    normalised = []
    for i, sc in enumerate(clips, 1):
        in_path = os.path.join(OUT_DIR, sc["clip"])
        out_path = os.path.join(OUT_DIR, f"_norm_{i:02d}.mp4")

        # Cut the dead "staring at camera" lead-in Kling puts on dialogue clips
        # (adaptive; leaves narration / already-talking clips untouched). Then
        # keep the rest of the clip's full length (no tail trim that could chop a
        # last word).
        full = audio_duration(in_path)
        start = speech_onset(in_path)
        dur = full - start

        # Alternate the zoom direction so the video breathes in and out.
        zoom_in = (i % 2 == 1)
        normalise(in_path, out_path, dur, target_w, target_h, zoom_in=zoom_in,
                  is_first=(i == 1), is_last=(i == len(clips)), start=start)
        normalised.append(out_path)
        trim_note = f", cut {start:.2f}s lead-in" if start > 0.05 else ""
        print(f"  [{i}] prepared {sc['clip']} -> {dur:.2f}s ({'zoom in' if zoom_in else 'zoom out'}{trim_note})")

    # 2) Join them all in order (hard cuts).
    list_file = os.path.join(OUT_DIR, "_concat.txt")
    with open(list_file, "w") as f:
        for p in normalised:
            f.write(f"file '{os.path.basename(p)}'\n")

    joined = os.path.join(OUT_DIR, "_joined.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", joined])
    print("  joined all clips")

    # 3) Add background music if a music file is present.
    if os.path.exists(MUSIC):
        print("  adding background music")
        run([
            "ffmpeg", "-y", "-i", joined, "-i", MUSIC,
            "-filter_complex",
            "[1:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=first[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", FINAL,
        ])
    else:
        os.replace(joined, FINAL)
        print("  (no output/music.mp3 found, skipped music)")

    # 4) Clean up the temporary pieces.
    for i in range(1, len(clips) + 1):
        p = os.path.join(OUT_DIR, f"_norm_{i:02d}.mp4")
        if os.path.exists(p):
            os.remove(p)
    for p in (list_file, joined):
        if os.path.exists(p):
            os.remove(p)

    print(f"\nDONE -> {FINAL}")
    print("Watch it: open output/final_video.mp4")


if __name__ == "__main__":
    main()
