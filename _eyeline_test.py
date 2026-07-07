"""THROWAWAY: does OmniHuman keep an OFF-CAMERA eyeline while talking?

The whole shot/reverse-shot plan depends on this. We make two portraits that look
OFF to the side (at an off-screen person, NOT at the camera), then lip-sync them
with OmniHuman and check: does the character keep looking sideways (good — reads
as talking TO the other person), or does OmniHuman snap the face to the lens (bad)?

Shot A: man looking screen-RIGHT.   Shot B: woman looking screen-LEFT.
Cut A->B should feel like they face each other.

Output: output/_eye_a.mp4 , output/_eye_b.mp4    (~$1.6 total)
Run:    python _eyeline_test.py
"""
import os, sys, subprocess, requests, fal_client
from dotenv import load_dotenv
import talking_clips
load_dotenv()
if not os.getenv("FAL_KEY"):
    sys.exit("FAL_KEY empty")

NB = "fal-ai/nano-banana-pro"

def trim(src, out, secs=4):
    subprocess.run(["ffmpeg","-y","-i",src,"-t",str(secs),"-c","copy",out],
                   check=True, capture_output=True)

def nano(prompt, out):
    r = fal_client.subscribe(NB, arguments={
        "prompt": prompt, "num_images":1, "aspect_ratio":"9:16", "resolution":"2K"},
        with_logs=False)
    open(out,"wb").write(requests.get(r["images"][0]["url"]).content)

# 1) short voice snippets (free)
trim("output/voice_sample_leonard_partridge.mp3", "output/_v_a.mp3", 4)
trim("output/voice_sample_harriet_crouch.mp3",    "output/_v_b.mp3", 4)

# 2) off-camera portraits
print("img A (man looking screen-right) ... $0.15")
nano("Vertical 9:16 portrait, a male lawyer in a sharp suit in a law office, "
     "three-quarter view, his head and eyes turned OFF to his RIGHT looking at "
     "another person just off-frame to the side. He is NOT looking at the camera. "
     "Cinematic moody lighting, photorealistic, upright vertical portrait.",
     "output/_eye_a.png")
print("img B (woman looking screen-left) ... $0.15")
nano("Vertical 9:16 portrait, a female lawyer in a sharp suit in a law office, "
     "three-quarter view, her head and eyes turned OFF to her LEFT looking at "
     "another person just off-frame to the side. She is NOT looking at the camera. "
     "Cinematic moody lighting, photorealistic, upright vertical portrait.",
     "output/_eye_b.png")

# 3) OmniHuman, reinforce the off-camera gaze in the prompt
print("OmniHuman A ... ~$0.64")
talking_clips.make_omnihuman_clip(
    "output/_eye_a.png", "output/_v_a.mp3",
    "the man keeps looking OFF to his right at the person he is talking to, "
    "three-quarter view, he does NOT look at the camera, speaking, subtle motion. "
    "Cinematic legal drama, photorealistic.",
    "output/_eye_a.mp4")
print("OmniHuman B ... ~$0.64")
talking_clips.make_omnihuman_clip(
    "output/_eye_b.png", "output/_v_b.mp3",
    "the woman keeps looking OFF to her left at the person she is talking to, "
    "three-quarter view, she does NOT look at the camera, speaking, subtle motion. "
    "Cinematic legal drama, photorealistic.",
    "output/_eye_b.mp4")

for f in ("output/_v_a.mp3","output/_v_b.mp3"):
    os.remove(f)
print("\nDONE. Watch _eye_a.mp4 then _eye_b.mp4 — do they look like they face EACH OTHER,")
print("or does OmniHuman turn their faces to the camera? ~$1.6 total.")
