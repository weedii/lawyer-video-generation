"""STAGE 2 - STEP 1: Write the SCENE script for the microdrama (scene-based).

NEW MODEL (Kling scene pipeline): the unit is a SCENE, not a single line. Each
scene is one cinematic shot where the characters ACT and talk TO EACH OTHER —
like a real short film — instead of one avatar talking to camera per line.

It reads the analyzed story + characters AND the raw scraped story, then writes
5-7 scenes with a real arc:
  - a NARRATION hook scene to open (voiceover over an establishing shot),
  - DIALOGUE scenes that dramatize the REAL events (two people in one room),
  - a NARRATION cliffhanger scene to close.

Usage:
    python scene_writer.py

Reads:  output/analysis.json   (characters + summary, from analyze.py)
        output/scraped.json     (the raw story facts, so dialogue is specific)
Output: adds a "script" section (with "scenes") INTO output/analysis.json
        and appends it to output/analysis.md so you can read it.
Cost:   ~$0.001 (cheap OpenAI model).
"""
import os
import sys
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
import costs

load_dotenv()

KEY = os.getenv("OPENAI_API_KEY")
if not KEY:
    sys.exit("ERROR: OPENAI_API_KEY is empty. Paste your OpenAI key in .env.")

MODEL = "gpt-4o-mini"
OUT_DIR = "output"

# HARD LIMIT from the video model: Kling generates at most TWO distinct voices
# per scene, so every dialogue scene may have AT MOST 2 speaking characters.
SYSTEM_PROMPT = """
You write short vertical TikTok microdramas for an audience of young lawyers,
based on a REAL legal news story. Write it as a SHORT FILM broken into SCENES —
each scene is one continuous shot where the characters act and talk TO EACH OTHER
in the same room, like real actors. This is NOT one person talking to camera.

Speakers:
- Use ONLY the given characters for dialogue. Write each "character" field EXACTLY
  as the name given in the cast — copy the name only, never add extra words.
- Some characters are marked "anonymous" inside their description — real but
  unnamed people (e.g. a junior colleague). You MAY use them as speakers.
- Also use a "Narrator" for the opening hook and closing cliffhanger (voiceover).

STRUCTURE (a real beginning, middle and end) — 5 to 7 SCENES total:
1. OPEN with exactly one NARRATION scene (beat "intro"): a punchy one-sentence
   voiceover hook that sets up the scandal.
2. MIDDLE: 3 to 5 DIALOGUE scenes that DRAMATIZE THE REAL EVENTS, building through
   the beats "setup" -> "escalation" -> "twist". Each scene is two characters
   confronting each other in one place.
3. CLOSE with exactly one NARRATION scene (beat "cliffhanger"): a strong button.

HARD RULES:
- A DIALOGUE scene has AT MOST TWO speaking characters (the video model allows
  only two voices per shot). Pick the two who matter for that beat.
- Each dialogue scene has 2 to 4 short lines total, alternating between the two
  characters so they actually talk to each other.
- Every DIALOGUE line must reference a CONCRETE fact from the story (a real event,
  place, document, ruling). No vague feelings-only lines.
- BANNED generic filler — NEVER write "trust me", "I won't abandon you", "was it
  worth it", "we can't give up", "I did what I had to". Forbidden.
- Keep the legal jargon (injunction, struck off, tribunal, inquest, rights of
  audience, etc.) and do NOT explain it.
- Set every scene in the REAL location where the drama happened.

FOR EACH SCENE also give:
- "shot": a cinematic shot + camera direction (e.g. "medium two-shot, slow dolly
  in", "over-the-shoulder close-up", "static wide").
- "action": the BLOCKING — what the characters physically DO in the shot and how
  they relate in space (e.g. "the father sits at the table gripping a file; the
  daughter stands over him, arms crossed, then leans in"). Describe the
  interaction and eyelines so they read as being in the same room.
- For DIALOGUE lines, an "emotion": one delivery cue (weary, smug, panicked,
  cold, defensive, contemptuous, ...).

Return ONLY valid JSON with exactly this shape:
{
  "title": "short episode title",
  "setting": "one line: the real place where this drama happens",
  "scenes": [
    {"type": "narration", "beat": "intro", "setting": "the real place", "shot": "shot + camera", "action": "what is seen in the establishing shot", "characters": [], "narration": "the voiceover hook", "dialogue": []},
    {"type": "dialogue", "beat": "setup", "setting": "the real place", "shot": "shot + camera", "action": "blocking: what the two characters physically do and how they face each other", "characters": ["Exact Name A", "Exact Name B"], "narration": "", "dialogue": [{"character": "Exact Name A", "line": "what is said", "emotion": "cue"}, {"character": "Exact Name B", "line": "reply", "emotion": "cue"}]}
  ]
}
"""


def clean_scenes(script: dict) -> dict:
    """Keep only well-formed scenes and fill any missing fields, so the rest of
    the pipeline always gets clean, predictable data. Also enforce the 2-speaker
    limit per dialogue scene (the video model's hard cap)."""
    cleaned = []
    for sc in script.get("scenes", []):
        if not isinstance(sc, dict):
            continue
        stype = sc.get("type", "dialogue")
        scene = {
            "type": stype,
            "beat": sc.get("beat", ""),
            "setting": sc.get("setting", script.get("setting", "")),
            "shot": sc.get("shot", ""),
            "action": sc.get("action", ""),
            "characters": [],
            "narration": (sc.get("narration") or "").strip(),
            "dialogue": [],
        }
        if stype == "narration":
            if not scene["narration"]:
                continue  # a narration scene with no voiceover is useless
        else:
            # Keep only proper dialogue lines.
            lines = [
                {
                    "character": d.get("character", ""),
                    "line": d["line"],
                    "emotion": d.get("emotion", ""),
                }
                for d in sc.get("dialogue", [])
                if isinstance(d, dict) and d.get("line") and d.get("character")
            ]
            if not lines:
                continue
            # Speaking characters, in first-appearance order, capped at TWO.
            order = []
            for d in lines:
                if d["character"] not in order:
                    order.append(d["character"])
            speakers = order[:2]
            # Drop any line whose speaker isn't one of the (max 2) kept speakers.
            lines = [d for d in lines if d["character"] in speakers]
            scene["characters"] = speakers
            scene["dialogue"] = lines
        cleaned.append(scene)
    script["scenes"] = cleaned
    return script


def leaked_names(script: dict, banned: list) -> list:
    """Check if any banned REAL name still appears anywhere in the script."""
    blob = json.dumps(script).lower()
    leaks = []
    for name in banned:
        if re.search(rf"\b{re.escape(name.lower())}\b", blob):
            leaks.append(name)
    return leaks


def write_script(data: dict, story_body: str):
    client = OpenAI(api_key=KEY, timeout=45.0, max_retries=3)

    # Cast list. Keep the NAME clean; anonymity marked INSIDE the parentheses.
    cast = "\n".join(
        f"- {c['fictional_name']} "
        f"({c['role']}{', anonymous' if c.get('anonymous') else ''}, "
        f"{c.get('gender', '')}): {c.get('personality', '')}"
        for c in data.get("characters", [])
    )
    banned = data.get("real_names", [])

    user_content = (
        f"REAL STORY (use these facts and details, but map any real people onto "
        f"the fictional cast below):\n{story_body}\n\n"
        f"SHORT SUMMARY:\n{data['summary']}\n\n"
        f"WHY IT MATTERS:\n{data.get('why_it_works', '')}\n\n"
        f"CAST (use only these names for dialogue):\n{cast}"
    )

    total_cost = 0.0
    extra = ""
    script = {}
    for attempt in range(2):
        ban_note = ""
        if banned:
            ban_note = (
                "\n\nBANNED NAMES — these REAL names appear in the source story. "
                "NEVER write any of them in ANY scene (narration or dialogue). Use "
                "ONLY the fictional cast names instead:\n"
                f"{', '.join(banned)}{extra}"
            )
        print(f"Writing the scene script with {MODEL} ... (attempt {attempt + 1})")
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + ban_note},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        total_cost += costs.openai_cost(
            resp.usage.prompt_tokens, resp.usage.completion_tokens
        )
        script = clean_scenes(json.loads(resp.choices[0].message.content))

        leaks = leaked_names(script, banned)
        if not leaks:
            break
        print(f"  Leaked real names {leaks}; rewriting ...")
        extra = (
            f"\nYou previously leaked these — they are STILL banned: "
            f"{', '.join(leaks)}. Replace each with the matching fictional character."
        )

    return script, total_cost


def append_markdown(script: dict, path: str):
    """Add a readable version of the scene script to analysis.md."""
    lines = [f"\n\n## Script: {script.get('title', '')}",
             f"*Setting: {script.get('setting', '')}*\n"]
    for i, sc in enumerate(script.get("scenes", []), 1):
        beat = sc.get("beat", "")
        lines.append(f"\n**Scene {i} ({beat}) — {sc.get('shot', '')}**")
        lines.append(f"_{sc.get('action', '')}_")
        if sc.get("type") == "narration":
            lines.append(f"- _Narrator:_ {sc.get('narration', '')}")
        else:
            for d in sc.get("dialogue", []):
                emo = f" [{d['emotion']}]" if d.get("emotion") else ""
                lines.append(f"- **{d['character']}**{emo}: {d['line']}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the Stage 1 pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    scraped_path = os.path.join(OUT_DIR, "scraped.json")
    story_body = data["summary"]
    if os.path.exists(scraped_path):
        with open(scraped_path) as f:
            story_body = json.load(f).get("body", story_body)

    script, cost = write_script(data, story_body)

    costs.record(data, "script", f"Write the scene script - OpenAI {MODEL}", cost)

    data["script"] = script
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    append_markdown(script, os.path.join(OUT_DIR, "analysis.md"))

    scenes = script.get("scenes", [])
    print(f"\nTitle: {script.get('title', '')}")
    print(f"Setting: {script.get('setting', '')}")
    print(f"Scenes: {len(scenes)}\n")
    for i, sc in enumerate(scenes, 1):
        if sc.get("type") == "narration":
            print(f"  Scene {i} ({sc.get('beat')}) NARRATION: {sc.get('narration')}")
        else:
            who = " + ".join(sc.get("characters", []))
            print(f"  Scene {i} ({sc.get('beat')}) DIALOGUE [{who}], "
                  f"{len(sc.get('dialogue', []))} lines")

    print("\nSaved scene script -> output/analysis.json (and analysis.md)")
    costs.show("OpenAI scene script", cost)
