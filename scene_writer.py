"""STAGE 2 - STEP 1: Write the scene script for the microdrama.

It reads the analyzed story + characters AND the raw scraped story, then writes
an organized scene with a real arc:
  - a NARRATION hook to open (set up the scandal),
  - dialogue that dramatizes the REAL events (built through story beats),
  - a NARRATION cliffhanger to close.

Usage:
    python scene_writer.py

Reads:  output/analysis.json   (characters + summary, from analyze.py)
        output/scraped.json     (the raw story facts, so dialogue is specific)
Output: adds a "script" section INTO output/analysis.json
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

# Rewritten prompt: forces a real story shape (intro -> arc -> cliffhanger),
# demands specific facts + jargon, and bans the generic filler we got before.
SYSTEM_PROMPT = """
You write short vertical TikTok microdramas for an audience of young lawyers,
based on a REAL legal news story. Make a tight, ORGANIZED scene with a clear arc
— not just two people arguing.

Speakers:
- Use ONLY the given characters (their exact names) for dialogue.
- Some characters are marked "(anonymous)" — these are real but unnamed people
  in the story (e.g. "Person A", a junior colleague). USE them as normal
  speakers with their exact label; they are essential to the back-and-forth.
- Also use a "Narrator" for short voiceover lines (the hook and the cliffhanger).

STRUCTURE (this is the whole point — give it a beginning, middle and end):
1. OPEN with exactly one narration line (beat "intro"): a punchy hook that sets
   up the scandal in one sentence, so the viewer instantly understands it.
2. MIDDLE: dialogue that DRAMATIZES THE REAL EVENTS from the story, building
   through the beats "setup" -> "escalation" -> "twist".
3. CLOSE with exactly one narration line (beat "cliffhanger"): a strong button
   that leaves the viewer wanting the next episode.

HARD RULES:
- Every DIALOGUE line must reference a CONCRETE fact from the story (a real
  event, place, document, ruling). No vague feelings-only lines.
- BANNED generic filler — NEVER write lines like "trust me", "I won't abandon
  you", "was it worth it", "we can't give up", "I did what I had to". Forbidden.
- Keep the legal jargon (injunction, rights of audience, struck off, tribunal,
  inquest, etc.) and do NOT explain it.
- 10 to 14 lines total, including the 2 narration lines.
- Set the scene in the REAL location where the drama actually happened.

PERFORMANCE (make it emotional, not flat — this is what stops it being boring):
- Give EVERY line an "emotion": ONE short delivery cue the voice can act, such as
  nervous, smug, angry, sarcastic, cold, panicked, resigned, amused, tense,
  defensive, contemptuous. Pick the one that fits the moment.
- You MAY also drop a short inline cue inside a line for a reaction, written in
  square brackets, e.g. "[scoffs] You actually believe that?" or
  "[sigh] I warned you." Use these sparingly, only when they add punch.
- Give EVERY line a "camera": a short shot direction for the video, such as
  "slow push-in", "static close-up", "slight handheld", or "quick zoom".

Return ONLY valid JSON with exactly this shape:
{
  "title": "short episode title",
  "setting": "one line: the real place where this scene happens",
  "lines": [
    {"type": "narration | dialogue", "character": "Narrator OR exact character name", "line": "what is said", "beat": "intro | setup | escalation | twist | cliffhanger", "emotion": "one delivery cue", "camera": "short shot direction"}
  ]
}
"""


def clean_lines(script: dict) -> dict:
    """The model sometimes slips a stray value into the lines list (valid JSON
    but wrong shape). Keep only proper line objects and fill any missing fields,
    so the rest of the pipeline always gets clean, predictable data."""
    cleaned = []
    for ln in script.get("lines", []):
        if not isinstance(ln, dict) or not ln.get("line"):
            continue  # skip stray strings / broken entries
        cleaned.append({
            "type": ln.get("type", "dialogue"),
            "character": ln.get("character", "Narrator"),
            "line": ln["line"],
            "beat": ln.get("beat", ""),
            # Performance cues: emotion drives the voice (voice_maker.py), camera
            # is kept for the video step. Default to empty so the rest of the
            # pipeline always gets these keys.
            "emotion": ln.get("emotion", ""),
            "camera": ln.get("camera", ""),
        })
    script["lines"] = cleaned
    return script


def leaked_names(script: dict, banned: list[str]) -> list[str]:
    """Check if any banned REAL name still appears anywhere in the script. Same
    whole-word check analyze.py uses, so 'Bar' inside 'Barrister' doesn't count."""
    blob = json.dumps(script).lower()
    leaks = []
    for name in banned:
        if re.search(rf"\b{re.escape(name.lower())}\b", blob):
            leaks.append(name)
    return leaks


def write_script(data: dict, story_body: str) -> tuple[dict, float]:
    # timeout: don't hang forever if OpenAI is slow. max_retries: auto-retry.
    client = OpenAI(api_key=KEY, timeout=45.0, max_retries=3)

    # Build the cast list the writer must use (name, role, gender, personality).
    # Mark anonymous people so the writer knows they are real speakers to include.
    cast = "\n".join(
        f"- {c['fictional_name']}{' (anonymous)' if c.get('anonymous') else ''} "
        f"({c['role']}, {c.get('gender', '')}): {c.get('personality', '')}"
        for c in data.get("characters", [])
    )

    # The REAL names that analyze.py already replaced. We reuse the SAME list so
    # the script can never leak a real name (e.g. the narrator saying "Griffiths"
    # instead of the fictional "Lawrence Cartwright").
    banned = data.get("real_names", [])

    # Give the model the REAL story facts (the scraped body) plus the summary,
    # so the dialogue can use specific events instead of generic emotion.
    # NOTE: the scraped story uses real names; the writer must use the fictional
    # cast names below, so we tell it to map real people onto the given cast.
    user_content = (
        f"REAL STORY (use these facts and details, but map any real people onto "
        f"the fictional cast below):\n{story_body}\n\n"
        f"SHORT SUMMARY:\n{data['summary']}\n\n"
        f"WHY IT MATTERS:\n{data.get('why_it_works', '')}\n\n"
        f"CAST (use only these names for dialogue):\n{cast}"
    )

    # Generate, then leak-check against the banned real names. Retry once if any
    # real name slipped through, telling the model exactly which to replace.
    total_cost = 0.0
    extra = ""
    script = {}
    for attempt in range(2):
        ban_note = ""
        if banned:
            ban_note = (
                "\n\nBANNED NAMES — these REAL names appear in the source story. "
                "NEVER write any of them in ANY line (narration or dialogue). Use "
                "ONLY the fictional cast names you are given instead:\n"
                f"{', '.join(banned)}{extra}"
            )
        print(f"Writing the script with {MODEL} ... (attempt {attempt + 1})")
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + ban_note},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,  # a bit more creative for drama
        )
        total_cost += costs.openai_cost(
            resp.usage.prompt_tokens, resp.usage.completion_tokens
        )
        script = clean_lines(json.loads(resp.choices[0].message.content))

        leaks = leaked_names(script, banned)
        if not leaks:
            break  # clean script, done
        # A real name slipped through — name them and retry once.
        print(f"  Leaked real names {leaks}; rewriting ...")
        extra = (
            f"\nYou previously leaked these — they are STILL banned: "
            f"{', '.join(leaks)}. Replace each with the matching fictional cast character."
        )

    return script, total_cost


def append_markdown(script: dict, path: str):
    """Add a readable version of the script to analysis.md, showing the beat of
    each line and marking narration vs dialogue."""
    lines = [f"\n\n## Script: {script.get('title', '')}",
             f"*Setting: {script.get('setting', '')}*\n"]
    for ln in script.get("lines", []):
        beat = ln.get("beat", "")
        emo = f" [{ln['emotion']}]" if ln.get("emotion") else ""   # show the delivery cue
        # Narration lines are voiceover; dialogue lines are spoken by a character.
        if ln.get("type") == "narration":
            lines.append(f"- _({beat}) Narrator:_{emo} {ln['line']}")
        else:
            lines.append(f"- **{ln['character']}** _({beat})_{emo}: {ln['line']}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the Stage 1 pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    # Load the raw scraped story so the writer has the real, specific facts.
    # If it is missing, fall back to just the summary.
    scraped_path = os.path.join(OUT_DIR, "scraped.json")
    story_body = data["summary"]
    if os.path.exists(scraped_path):
        with open(scraped_path) as f:
            story_body = json.load(f).get("body", story_body)

    script, cost = write_script(data, story_body)

    # Save the script back INTO analysis.json (everything in one place).
    data["script"] = script
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Add a readable copy to analysis.md.
    append_markdown(script, os.path.join(OUT_DIR, "analysis.md"))

    # Show what we got, marking narration lines and beats.
    print(f"\nTitle: {script.get('title', '')}")
    print(f"Setting: {script.get('setting', '')}")
    print(f"Lines: {len(script.get('lines', []))}\n")
    for ln in script.get("lines", []):
        who = "Narrator" if ln.get("type") == "narration" else ln["character"]
        print(f"  ({ln.get('beat', '')}) {who}: {ln['line']}")

    print("\nSaved script -> output/analysis.json (and analysis.md)")
    costs.show("OpenAI script", cost)
