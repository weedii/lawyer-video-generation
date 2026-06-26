"""STAGE 2 - STEP 5: Assemble the final microdrama video.

It joins the clips, in script order, into one vertical video and (optionally)
adds background music. No captions.

Quality first: the clips can be high resolution (e.g. OmniHuman is 1088x1920).
So we DON'T downscale them — we find the biggest clip size and render the whole
video at that size and frame rate, re-encoding at near-lossless quality.

Motion: a gentle zoom that alternates in/out per clip so the video breathes.
It is done on a 2x-SUPERSAMPLED frame at the clips' native size, so it stays
smooth (no jitter) AND sharp (no quality loss).

Clean joins (all FREE, ffmpeg):
  - a tiny audio fade at every clip edge, to remove the click/pop at a hard cut;
  - a fade-from-black at the start and fade-to-black at the end.

Usage:
    python assemble.py

Reads:  output/analysis.json   (script lines, each with a "clip" file)
        output/music.mp3        (OPTIONAL background music; used if present)
Output: output/final_video.mp4
Cost:   free (runs locally with ffmpeg).
"""
import os
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


def run(cmd: list[str]):
    """Run an ffmpeg command and stop on error."""
    subprocess.run(cmd, check=True, capture_output=True)


def audio_duration(path: str) -> float:
    """Return the length (seconds) of an audio (or video) file."""
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def video_dims(path: str) -> tuple[int, int]:
    """Return (width, height) of a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def normalise(in_path: str, out_path: str, dur: float, w: int, h: int,
              zoom_in: bool = True, is_first: bool = False, is_last: bool = False):
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

    run([
        "ffmpeg", "-y", "-i", in_path,
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

    lines = (data.get("script") or {}).get("lines", [])
    clips = [ln for ln in lines if ln.get("clip")]
    if not clips:
        sys.exit("No talking clips found. Run talking_clips.py first.")

    # Pick the target size = the BIGGEST clip, so we never downscale good clips.
    paths = [os.path.join(OUT_DIR, ln["clip"]) for ln in clips]
    dims = [video_dims(p) for p in paths]
    target_w, target_h = max(dims, key=lambda wh: wh[0] * wh[1])
    print(f"Assembling {len(clips)} clips at {target_w}x{target_h} ...")

    # 1) Normalise each clip to the common size/fps/quality (no zoom, no downscale).
    normalised = []
    for i, ln in enumerate(clips, 1):
        in_path = os.path.join(OUT_DIR, ln["clip"])
        out_path = os.path.join(OUT_DIR, f"_norm_{i:02d}.mp4")

        # Only trim when a clip is MUCH longer than its speech (Kling pads with
        # ~3s of dead air). OmniHuman already matches the audio, so trimming to
        # the mp3 length would chop a few ms off the real ending -> bad cut.
        clip_dur = audio_duration(in_path)
        audio_dur = (audio_duration(os.path.join(OUT_DIR, ln["audio"]))
                     if ln.get("audio") else clip_dur)
        dur = audio_dur if (clip_dur - audio_dur) > 0.5 else clip_dur

        # Alternate the zoom direction so the video breathes in and out.
        zoom_in = (i % 2 == 1)
        normalise(in_path, out_path, dur, target_w, target_h, zoom_in=zoom_in,
                  is_first=(i == 1), is_last=(i == len(clips)))
        normalised.append(out_path)
        print(f"  [{i}] prepared {ln['clip']} -> {dur:.2f}s ({'zoom in' if zoom_in else 'zoom out'})")

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
