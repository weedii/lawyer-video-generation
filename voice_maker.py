"""STAGE 2 - STEP 2: Voices for the scene pipeline.

NEW MODEL (Kling scene pipeline). Two jobs:
  1. For every SPEAKING character: generate one ElevenLabs voice SAMPLE, then
     CLONE it into a Kling voice_id (create-voice). Kling then speaks that
     character's dialogue in OUR voice, and the SAME voice_id is reused in every
     scene, so a character sounds identical across the whole film (voice
     consistency — the thing plain scene models can't do).
  2. For every NARRATION scene: generate the Narrator's ElevenLabs voiceover mp3
     (played over the establishing shot by scene_clips.py).

Usage:
    python voice_maker.py

Reads:  output/analysis.json   (needs the "script" section from scene_writer.py)
Output: - writes c["voice_id"] (ElevenLabs) and c["kling_voice_id"] onto each
          speaking character in analysis.json
        - writes scene["audio"] (narrator mp3) onto each narration scene
        - saves voice_sample_<name>.mp3 and voice_narr_<n>.mp3 in output/
Cost:   ElevenLabs by characters + a one-time Kling clone per character.
"""
import os
import sys
import json
import re
import subprocess
import requests
import fal_client
from dotenv import load_dotenv
import costs

load_dotenv()

KEY = os.getenv("ELEVENLABS_API_KEY")
if not KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty. Paste your key in .env.")
if not os.getenv("FAL_KEY"):
    sys.exit("ERROR: FAL_KEY is empty (needed to clone voices into Kling).")

OUT_DIR = "output"
MODEL_ID = "eleven_v3"                 # expressive ElevenLabs model
CREATE_VOICE_MODEL = "fal-ai/kling-video/create-voice"   # clone -> voice_id

# Ready-made ElevenLabs voices by gender; each character keeps one.
FEMALE_VOICES = ["EXAVITQu4vr4xnSDxMaL", "cgSgspJ2msm6clMCkdW9"]
MALE_VOICES = ["JBFqnCBsd6RMkjVDRZzb", "nPczCjzI2devNBz1zQrb"]
NARRATOR_VOICE = "onwK4e9ZLuTAKqWW03F9"   # Daniel — deep, documentary narrator


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def character_gender(character: dict) -> str:
    gender = str(character.get("gender", "")).strip().lower()
    if gender in ("male", "female"):
        return gender
    text = (f"{character.get('role','')} {character.get('appearance','')} "
            f"{character.get('personality','')}").lower()
    if any(w in text for w in ["woman", "female", "she ", "her ", "daughter", "mrs", "ms "]):
        return "female"
    return "male"


def trim_trailing_silence(path: str):
    """Cut silence off the END of an mp3, keeping a tiny 0.1s tail (see below)."""
    tmp = path + ".trim.mp3"
    r = subprocess.run([
        "ffmpeg", "-y", "-i", path, "-af",
        "areverse,silenceremove=start_periods=1:start_threshold=-50dB:"
        "start_silence=0.1,areverse", tmp,
    ], capture_output=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)


def make_voice(text: str, voice_id: str, out_path: str):
    """Send one line to ElevenLabs (v3) and save the mp3. v3 clips the final word,
    so we append a trailing ' —' (the cut lands on the dash) then trim the silence."""
    buffered = text.rstrip()
    if buffered and buffered[-1] not in "—-…":
        buffered += " —"
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": KEY, "Content-Type": "application/json"},
        json={"text": buffered, "model_id": MODEL_ID},
    )
    if resp.status_code != 200:
        sys.exit(f"ElevenLabs error {resp.status_code}: {resp.text}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    trim_trailing_silence(out_path)


def clone_to_kling(sample_path: str) -> str:
    """Upload an ElevenLabs voice sample and clone it into a reusable Kling
    voice_id (create-voice). Returns the voice_id, or "" on failure."""
    url = fal_client.upload_file(sample_path)
    r = fal_client.subscribe(CREATE_VOICE_MODEL, arguments={"voice_url": url},
                             with_logs=False)
    return r.get("voice_id") or r.get("id") or (r.get("data") or {}).get("voice_id", "")


def speaking_characters(scenes: list) -> list:
    """Ordered, unique list of character names that actually speak in dialogue."""
    order = []
    for sc in scenes:
        if sc.get("type") == "dialogue":
            for d in sc.get("dialogue", []):
                if d["character"] not in order:
                    order.append(d["character"])
    return order


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

    total_chars = 0     # ElevenLabs character count (for cost estimate)
    clones = 0          # how many Kling voices we cloned

    # --- 1. One cloned voice per speaking character ------------------------
    speakers = speaking_characters(scenes)
    print(f"Cloning voices for {len(speakers)} speaking characters ...")
    f_i = m_i = 0
    for name in speakers:
        c = chars_by_name.get(name)
        if not c:
            print(f"  note: speaker '{name}' not in cast, skipping clone.")
            continue
        # Assign a gender-matched ElevenLabs voice.
        if character_gender(c) == "female":
            eleven = FEMALE_VOICES[f_i % len(FEMALE_VOICES)]; f_i += 1
        else:
            eleven = MALE_VOICES[m_i % len(MALE_VOICES)]; m_i += 1
        c["voice_id"] = eleven

        # Build a clone SAMPLE from the character's own lines (must be >=5s for
        # create-voice); pad with a neutral line if their dialogue is too short.
        own_lines = [d["line"] for sc in scenes if sc.get("type") == "dialogue"
                     for d in sc["dialogue"] if d["character"] == name]
        sample_text = " ".join(own_lines)
        if len(sample_text) < 200:      # ~ under ~12s of speech: pad it
            sample_text += (" I have spent my whole career at the Bar, and I know "
                            "exactly how these proceedings work.")
        sample_path = os.path.join(OUT_DIR, f"voice_sample_{slug(name)}.mp3")
        make_voice(sample_text, eleven, sample_path)
        total_chars += len(sample_text)

        kling_id = clone_to_kling(sample_path)
        c["kling_voice_id"] = kling_id
        clones += 1
        print(f"  {name}: ElevenLabs {eleven} -> Kling voice_id {kling_id}")

    # --- 2. Narrator voiceover for each narration scene -------------------
    n = 0
    for i, sc in enumerate(scenes, 1):
        if sc.get("type") != "narration":
            continue
        n += 1
        text = sc.get("narration", "")
        file_name = f"voice_narr_{i:02d}.mp3"
        make_voice(text, NARRATOR_VOICE, os.path.join(OUT_DIR, file_name))
        sc["audio"] = file_name
        total_chars += len(text)
        print(f"  Narration scene {i}: saved {file_name}")

    # --- Cost (ElevenLabs by characters + Kling clone fee) ----------------
    est = (total_chars / 1000 * costs.ELEVENLABS_PER_1K_CHARS
           + clones * costs.KLING_CREATE_VOICE_PER)
    costs.record(data, "voices",
                 f"Voices - ElevenLabs {MODEL_ID} (~{total_chars} chars) + "
                 f"{clones} Kling voice clones", est)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated analysis.json with {clones} cloned voice_ids + {n} narrator lines.")
    costs.show(f"voices ({total_chars} chars, {clones} clones)", est)


if __name__ == "__main__":
    main()
