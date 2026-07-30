"""STAGE 2 - STEP 2: Assign a fixed voice to every character (Seedance + our-voice pipeline).

The scene clips are made by Seedance (which invents its own voice per clip) and then
RE-VOICED into OUR voice with ElevenLabs Speech-to-Speech (in scene_clips.py).
For that we just need to decide, ONCE, which ElevenLabs voice each character owns
— the same voice every time, so a character sounds identical across the whole
film. This step does only that: it writes c["voice_id"] onto each speaking
character (and the narrating protagonist) in analysis.json.

No audio is generated here and nothing is cloned, so this step is effectively
free; the real voice cost (the Speech-to-Speech swap) is billed in scene_clips.py.

Usage:
    python voice_maker.py

Reads:  output/analysis.json   (needs the "script" section from scene_writer.py)
Output: writes c["voice_id"] (ElevenLabs) onto each speaking character.
Cost:   free (voice IDs only).
"""
import os
import sys
import json
import re
from dotenv import load_dotenv
import costs

load_dotenv()

OUT_DIR = "output"

# Ready-made ElevenLabs voices by gender; each character keeps one for the whole
# film (swap these IDs for your own cloned brand voices later if you want).
FEMALE_VOICES = ["EXAVITQu4vr4xnSDxMaL", "cgSgspJ2msm6clMCkdW9"]
MALE_VOICES = ["JBFqnCBsd6RMkjVDRZzb", "nPczCjzI2devNBz1zQrb"]


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


def speaking_characters(scenes: list) -> list:
    """Ordered, unique list of everyone who needs a voice: the dialogue speakers
    PLUS the protagonist(s) who perform the narration (memoir, first person)."""
    order = []
    for sc in scenes:
        if sc.get("type") == "dialogue":
            for d in sc.get("dialogue", []):
                if d["character"] not in order:
                    order.append(d["character"])
        elif sc.get("type") == "narration":
            for name in sc.get("characters", []):
                if name not in order:
                    order.append(name)
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

    # Give each speaking character a fixed, gender-matched ElevenLabs voice.
    speakers = speaking_characters(scenes)
    print(f"Assigning voices to {len(speakers)} speaking characters ...")
    f_i = m_i = 0
    for name in speakers:
        c = chars_by_name.get(name)
        if not c:
            print(f"  note: speaker '{name}' not in cast, skipping.")
            continue
        if character_gender(c) == "female":
            vid = FEMALE_VOICES[f_i % len(FEMALE_VOICES)]; f_i += 1
        else:
            vid = MALE_VOICES[m_i % len(MALE_VOICES)]; m_i += 1
        c["voice_id"] = vid
        tag = " (narrator)" if any(
            sc.get("type") == "narration" and name in sc.get("characters", [])
            for sc in scenes) else ""
        print(f"  {name}{tag}: ElevenLabs {vid}")

    # Free step (IDs only); the Speech-to-Speech re-voicing is billed in scene_clips.
    costs.record(data, "voices",
                 "Voices - ElevenLabs voice IDs assigned (re-voicing billed in clips)", 0.0)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated analysis.json with a fixed voice_id per character.")
    costs.show("voice assignment (free)", 0.0)


if __name__ == "__main__":
    main()
