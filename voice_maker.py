"""STAGE 2 - STEP 2: Make the voices for the scene.

It reads the scene script, gives EACH character one fixed voice (so they sound
the same the whole video), and saves one audio file per spoken line.

Usage:
    python voice_maker.py

Reads:  output/analysis.json   (needs the "script" section from scene_writer.py)
Output: output/voice_01_<name>.mp3, voice_02_<name>.mp3, ...   (one per line)
        It also writes the voice + audio file names back INTO analysis.json.
Cost:   ElevenLabs bills by characters. We print how many.
"""
import os
import sys
import json
import re
import requests
from dotenv import load_dotenv
import costs

load_dotenv()

KEY = os.getenv("ELEVENLABS_API_KEY")
if not KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is empty. Paste your key in .env.")

OUT_DIR = "output"

# Ready-made ElevenLabs voices, split by gender. Each character gets one and
# keeps it for the whole video. If there are more same-gender characters than
# voices, we cycle through the list.
FEMALE_VOICES = [
    "EXAVITQu4vr4xnSDxMaL",  # Sarah - young, crisp
    "cgSgspJ2msm6clMCkdW9",  # Jessica - warm
]
MALE_VOICES = [
    "JBFqnCBsd6RMkjVDRZzb",  # George - deep, steady
    "nPczCjzI2devNBz1zQrb",  # Brian - calm
]

# Fixed voice for the Narrator (the intro hook + the cliffhanger voiceover).
# Daniel - deep, authoritative, documentary/narration feel.
NARRATOR_VOICE = "onwK4e9ZLuTAKqWW03F9"


def slug(name: str) -> str:
    """Turn 'Leo Finnegan' into 'leo_finnegan' for safe file names."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def character_gender(character: dict) -> str:
    """Use the explicit 'gender' field from analyze.py. If it is missing (older
    files), fall back to guessing from the description. Checks female first
    because the word 'woman' contains 'man'."""
    gender = str(character.get("gender", "")).strip().lower()
    if gender in ("male", "female"):
        return gender

    text = (
        f"{character.get('role', '')} {character.get('appearance', '')} "
        f"{character.get('personality', '')}"
    ).lower()
    if any(w in text for w in ["woman", "female", "she ", "her ", "daughter", "mrs", "ms "]):
        return "female"
    return "male"  # default to male if unclear


def assign_voices(characters: list[dict]) -> dict:
    """Give each character one fixed voice id. Returns {name: voice_id}."""
    mapping = {}
    f_i, m_i = 0, 0
    for c in characters:
        name = c["fictional_name"]
        if character_gender(c) == "female":
            mapping[name] = FEMALE_VOICES[f_i % len(FEMALE_VOICES)]
            f_i += 1
        else:
            mapping[name] = MALE_VOICES[m_i % len(MALE_VOICES)]
            m_i += 1
        c["voice_id"] = mapping[name]  # save it on the character too
    return mapping


def make_voice(text: str, voice_id: str, out_path: str):
    """Send one line to ElevenLabs and save the mp3."""
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": KEY, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
    )
    if resp.status_code != 200:
        sys.exit(f"ElevenLabs error {resp.status_code}: {resp.text}")
    with open(out_path, "wb") as f:
        f.write(resp.content)


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    script = data.get("script")
    if not script or not script.get("lines"):
        sys.exit("No script found. Run scene_writer.py first.")

    # Step 1: give every character a fixed voice.
    voices = assign_voices(data.get("characters", []))
    print("Voice for each character:")
    for name, vid in voices.items():
        print(f"  {name} -> {vid}")
    print(f"  Narrator -> {NARRATOR_VOICE}\n")

    # Step 2: make one audio file per line, in order.
    lines = script["lines"]
    total_chars = 0
    print(f"Making {len(lines)} voice lines ...")
    for i, ln in enumerate(lines, 1):
        name = ln["character"]
        # Narration lines use the fixed narrator voice (the narrator is not part
        # of the cast). Everyone else uses their assigned character voice.
        # Anonymous people (Person A/B) ARE in the cast, so assign_voices already
        # gave them a gender-matched voice here — they are voiced like anyone else.
        if ln.get("type") == "narration" or name == "Narrator":
            voice_id = NARRATOR_VOICE
        else:
            voice_id = voices.get(name)
        if not voice_id:
            # The script named someone not in the cast. Don't drop the line
            # (that silently breaks the conversation) — voice it with the
            # narrator voice so it is still heard.
            print(f"  [{i}] note: '{name}' not in cast, using narrator voice.")
            voice_id = NARRATOR_VOICE

        file_name = f"voice_{i:02d}_{slug(name)}.mp3"
        out_path = os.path.join(OUT_DIR, file_name)
        make_voice(ln["line"], voice_id, out_path)

        ln["audio"] = file_name          # remember the audio file for this line
        total_chars += len(ln["line"])
        print(f"  [{i}] {name}: saved {file_name}")

    # Save the voice + audio info back into analysis.json (one place for all).
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    est = total_chars / 1000 * costs.ELEVENLABS_PER_1K_CHARS
    print("\nUpdated output/analysis.json with voices + audio files.")
    costs.show(f"voices ({total_chars} characters, estimate)", est)


if __name__ == "__main__":
    main()
