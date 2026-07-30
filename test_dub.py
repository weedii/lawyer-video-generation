"""ONE-OFF test: re-dub an existing 2-person Seedance clip with correct ElevenLabs
voices via Sync Lipsync 2.0. Proves whether the two-face mapping + correct words work
BEFORE we change the pipeline. Disposable.
"""
import os, subprocess, requests, fal_client
from dotenv import load_dotenv
load_dotenv()
K = os.environ["ELEVENLABS_API_KEY"]
OUT = "output"
CLIP = os.path.join(OUT, "clip_02.mp4")

# scene 2, in on-screen turn order (Leonard speaks first, then the associate)
LINES = [
    ("JBFqnCBsd6RMkjVDRZzb", "She'd make a nice addition to the office, don't you think?"),
    ("EXAVITQu4vr4xnSDxMaL", "Book a hotel on the force card next time, Leonard."),
]

def tts(voice_id, text, path):
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        headers={"xi-api-key": K},
        json={"text": text, "model_id": "eleven_multilingual_v2"}, timeout=120)
    r.raise_for_status()
    open(path, "wb").write(r.content)

def silence(sec, path):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(sec), "-b:a", "128k", path], check=True, capture_output=True)

# 1) make each line in its own voice
tts(*LINES[0], os.path.join(OUT, "_l1.mp3"))
tts(*LINES[1], os.path.join(OUT, "_l2.mp3"))
# 2) build ONE track that follows the clip's turns: pause, line1, pause, line2
silence(0.4, os.path.join(OUT, "_s1.mp3"))
silence(0.7, os.path.join(OUT, "_s2.mp3"))
combined = os.path.join(OUT, "_dub_audio.mp3")
subprocess.run(["ffmpeg", "-y",
                "-i", os.path.join(OUT, "_s1.mp3"), "-i", os.path.join(OUT, "_l1.mp3"),
                "-i", os.path.join(OUT, "_s2.mp3"), "-i", os.path.join(OUT, "_l2.mp3"),
                "-filter_complex", "[0][1][2][3]concat=n=4:v=0:a=1", combined],
               check=True, capture_output=True)
dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", combined], capture_output=True, text=True).stdout.strip()
print(f"combined voice track = {dur}s (clip is 8.05s)")

# 3) Sync Lipsync 2.0 re-dub
print("uploading + running Sync Lipsync 2.0 ...")
res = fal_client.subscribe("fal-ai/sync-lipsync/v2", arguments={
    "video_url": fal_client.upload_file(CLIP),
    "audio_url": fal_client.upload_file(combined),
    "model": "lipsync-2", "sync_mode": "cut_off"}, with_logs=False)
v = res.get("video") or {}
url = v.get("url") if isinstance(v, dict) else v
open(os.path.join(OUT, "test_dub_clip02.mp4"), "wb").write(requests.get(url, timeout=300).content)
print("saved output/test_dub_clip02.mp4")
