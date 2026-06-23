"""STAGE 2 - STEP 5: Assemble the final microdrama video.

It joins all the talking clips (in script order) into one vertical video, and
(optionally) adds background music. No captions.

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


def run(cmd: list[str]):
    """Run an ffmpeg command and stop on error."""
    subprocess.run(cmd, check=True, capture_output=True)


def normalise(in_path: str, out_path: str):
    """Re-encode a clip to the same format as all the others, so they can be
    joined together cleanly."""
    run([
        "ffmpeg", "-y", "-i", in_path,
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100",
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

    # 1) Normalise each clip to a common format.
    normalised = []
    for i, ln in enumerate(clips, 1):
        in_path = os.path.join(OUT_DIR, ln["clip"])
        out_path = os.path.join(OUT_DIR, f"_norm_{i:02d}.mp4")
        normalise(in_path, out_path)
        normalised.append(out_path)
        print(f"  [{i}] prepared {ln['clip']}")

    # 2) Join them all in order.
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
