"""STAGE 2 - STEP 2: Assign voices (memoir VOICEOVER pipeline).

In the voiceover style only ONE voice is ever heard: the NARRATOR (the lead). This
step gives the narrator a voice and assigns the other characters a voice_id too
(harmless — they're never heard, since there's no on-screen dialogue).

The narrator voice is picked at RANDOM for each video, from the whole ElevenLabs
account, matched to the lead's gender — so the channel doesn't sound like the same
person every time (NARRATOR_RANDOM). Set NARRATOR_RANDOM = False and NARRATOR_VOICE
to a fixed ID to pin one instead.

No audio is generated here (audio_maker.py does the TTS voiceover in the next step), so
this step is effectively free — it only listens to the account's voice list and writes voice_ids.

Usage:
    python voice_maker.py

Reads:  output/analysis.json   (needs the "script" section from scene_writer.py)
Output: writes c["voice_id"] (ElevenLabs) onto each character; the narrator gets a
        random gender-matched voice.
Cost:   free (voice IDs only).
"""
import os
import sys
import json
import re
import random
import requests
from dotenv import load_dotenv
import costs

load_dotenv()
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")

OUT_DIR = "output"

# Ready-made ElevenLabs voices by gender; each character keeps one for the whole
# film (swap these IDs for your own cloned brand voices later if you want).
FEMALE_VOICES = ["EXAVITQu4vr4xnSDxMaL", "cgSgspJ2msm6clMCkdW9"]
MALE_VOICES = ["JBFqnCBsd6RMkjVDRZzb", "nPczCjzI2devNBz1zQrb"]

# The NARRATOR (the lead) is the ONLY voice the viewer hears in the memoir style. We pick a
# DIFFERENT narrator voice at RANDOM for each video (so the channel doesn't sound like the
# same person every time) from ALL the voices on the ElevenLabs account, matched to the
# lead's gender. Set NARRATOR_RANDOM = False and NARRATOR_VOICE = "<id>" to pin one instead.
NARRATOR_RANDOM = True
NARRATOR_VOICE = None

# Fallback voice pools (premade ElevenLabs IDs) used only if listing the account's voices
# fails, so a video can still be made offline from the API's voice library.
FALLBACK_MALE = ["JBFqnCBsd6RMkjVDRZzb", "nPczCjzI2devNBz1zQrb", "pNInz6obpgDQGcFmaJgB",
                 "TxGEqnHWrfWFTfGW9XjX", "onwK4e9ZLuTAKqWW03F9", "pqHfZKP75CvOlQylNhV4"]
FALLBACK_FEMALE = ["EXAVITQu4vr4xnSDxMaL", "cgSgspJ2msm6clMCkdW9", "XrExE9yKIg1WjnnlVkGX",
                   "pFZP5JQG7iQjIQuC4Bku", "Xb7hH8MSUJpSbSDYk0k2"]


def account_voices(gender: str) -> list:
    """All voice_ids on the ElevenLabs account that match `gender`, so we can pick a fresh
    narrator at random each video from the FULL library (not a hard-coded five or six).
    Reads the voice's own gender label; if none match, returns every voice. Falls back to
    the built-in pool if the account can't be listed."""
    try:
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": ELEVEN_KEY}, timeout=30)
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            matched = [v["voice_id"] for v in voices
                       if (v.get("labels") or {}).get("gender", "").lower() == gender]
            allv = [v["voice_id"] for v in voices]
            if matched:
                return matched
            if allv:
                return allv
        else:
            print(f"  (voice list failed {r.status_code}; using fallback pool)")
    except Exception as e:
        print(f"  (could not list account voices: {e}; using fallback pool)")
    return FALLBACK_MALE if gender == "male" else FALLBACK_FEMALE


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

    # Give the NARRATOR (lead = first non-anonymous character) their voice — this is the ONLY
    # voice the viewer hears. Default: a DIFFERENT random voice each video, from the whole
    # account library, matched to the lead's gender, so the channel varies video to video.
    lead = next((c["fictional_name"] for c in data.get("characters", [])
                 if not c.get("anonymous")), None)
    if lead and chars_by_name.get(lead):
        if NARRATOR_RANDOM:
            g = character_gender(chars_by_name[lead])
            vid = random.choice(account_voices(g))
            chars_by_name[lead]["voice_id"] = vid
            print(f"  narrator {lead}: random {g} voice {vid}")
        elif NARRATOR_VOICE:
            chars_by_name[lead]["voice_id"] = NARRATOR_VOICE
            print(f"  narrator {lead}: pinned voice {NARRATOR_VOICE}")

    # Free step (IDs only); the Speech-to-Speech re-voicing is billed in scene_clips.
    costs.record(data, "voices",
                 "Voices - ElevenLabs voice IDs assigned (re-voicing billed in clips)", 0.0)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nUpdated analysis.json with a fixed voice_id per character.")
    costs.show("voice assignment (free)", 0.0)


if __name__ == "__main__":
    main()
