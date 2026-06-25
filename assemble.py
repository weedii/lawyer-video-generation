"""STAGE 2 - STEP 5: Assemble the final microdrama video.

It trims each clip to its audio length (the talking model pads short clips),
then joins them with clean, professional cuts and (optionally) background music.
No captions.

Editing approach (researched best practice for short-form dialogue):
  - HARD CUTS between lines. For talking dialogue this looks far more
    professional than crossfades/dissolves, which feel slow and amateur.
  - The polish is in the details, NOT camera movement (a moving frame looked
    like a shaky handheld, so the camera never moves):
      * a tiny audio fade at every clip edge, which removes the click/pop you
        otherwise hear at each hard cut;
      * a fade-from-black at the very start and fade-to-black at the very end,
        so the video opens and closes cleanly instead of snapping on/off.

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

# How long the opening fade-in and closing fade-out (from/to black) last.
OPEN_FADE = 0.3
CLOSE_FADE = 0.4
# Tiny fade on every clip edge to kill the pop/click at a hard cut (40 ms).
EDGE_FADE = 0.04

# Gentle zoom. Clips alternate slow zoom-IN / zoom-OUT so the video "breathes"
# instead of sitting still — but only a little (6%) and very slowly.
# KEY for smoothness: we first upscale to 2x (supersample), then zoom. The old
# shaky feel came from zoompan rounding to whole pixels each frame; zooming on a
# 2x-bigger image makes that rounding half a pixel, so the move is smooth.
ZOOM_AMOUNT = 0.06          # 6% total zoom over the whole clip
SUPER_W, SUPER_H = 1440, 2560   # 2x of 720x1280, for jitter-free zoom


def run(cmd: list[str]):
    """Run an ffmpeg command and stop on error."""
    subprocess.run(cmd, check=True, capture_output=True)


def audio_duration(path: str) -> float:
    """Return the length (seconds) of an audio (or video) file."""
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def normalise(in_path: str, out_path: str, dur: float, zoom_in: bool = True,
              is_first: bool = False, is_last: bool = False):
    """Re-encode one clip to a common format and apply the clean-cut polish.

    dur: cut the clip to exactly this length (its audio length). The talking
    model (Kling) pads short clips to a fixed ~7.2s block, so we trim the dead
    tail here so each clip matches its speech.

    Video: fit to a vertical frame and apply a slow, smooth zoom (in or out, see
    zoom_in) so the shot gently breathes. The very first clip fades in from
    black; the very last fades out to black.

    Audio: a tiny fade in/out on EVERY clip removes the click/pop heard at a
    hard cut. -ac 2 forces stereo on every clip (narrator clips are mono,
    dialogue clips stereo; mixing layouts loses audio on some segments)."""
    # Linear zoom across the whole clip, expressed per output frame so the
    # endpoints are exact and the motion is perfectly even (no easing wobble).
    frames = max(int(round(dur * 30)) - 1, 1)
    if zoom_in:
        zexpr = f"1.0+{ZOOM_AMOUNT}*on/{frames}"                  # 1.00 -> 1.06
    else:
        zexpr = f"{1.0 + ZOOM_AMOUNT}-{ZOOM_AMOUNT}*on/{frames}"  # 1.06 -> 1.00

    # --- video filter ---
    # Upscale to 2x first (supersample), then zoom on that big image and output
    # back at 720x1280 — this is what keeps the slow zoom smooth, not jittery.
    vf = (
        f"scale={SUPER_W}:{SUPER_H}:force_original_aspect_ratio=increase,"
        f"crop={SUPER_W}:{SUPER_H},"
        f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"fps=30:s=720x1280"
    )
    if is_first:
        vf += f",fade=t=in:st=0:d={OPEN_FADE}"            # open from black
    if is_last:
        vf += f",fade=t=out:st={max(dur - CLOSE_FADE, 0):.3f}:d={CLOSE_FADE}"  # close to black

    # --- audio filter ---
    # Bigger fade at the very start/end (matches the black open/close); a tiny
    # one everywhere else so no cut ever pops.
    a_in = OPEN_FADE if is_first else EDGE_FADE
    a_out = CLOSE_FADE if is_last else EDGE_FADE
    af = (f"afade=t=in:st=0:d={a_in},"
          f"afade=t=out:st={max(dur - a_out, 0):.3f}:d={a_out}")

    run([
        "ffmpeg", "-y", "-i", in_path,
        "-t", f"{dur:.3f}",                 # trim the padded tail to the audio length
        "-vf", vf, "-af", af,
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
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

    print(f"Assembling {len(clips)} clips ...")

    # 1) Normalise each clip: trim to its audio length, stable framing, and the
    #    clean-cut polish (edge audio fades + black open/close on the ends).
    normalised = []
    for i, ln in enumerate(clips, 1):
        in_path = os.path.join(OUT_DIR, ln["clip"])
        out_path = os.path.join(OUT_DIR, f"_norm_{i:02d}.mp4")

        # Length = this line's audio length (falls back to the clip's own length).
        if ln.get("audio"):
            dur = audio_duration(os.path.join(OUT_DIR, ln["audio"]))
        else:
            dur = audio_duration(in_path)

        # Alternate the zoom direction per clip so the video gently breathes
        # in and out instead of always pushing the same way.
        zoom_in = (i % 2 == 1)
        normalise(in_path, out_path, dur, zoom_in=zoom_in,
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
