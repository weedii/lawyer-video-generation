"""STAGE 2 - STEP 1: Write the scene script for the microdrama.

It reads the analyzed story + characters, then writes the script for the scene:
who says what, line by line, with on-screen captions. It follows the microdrama
formula: hook -> conflict -> escalation -> cliffhanger.

Usage:
    python scene_writer.py

Reads:  output/analysis.json   (from analyze.py / gen_characters.py)
Output: adds a "script" section INTO output/analysis.json
        and appends it to output/analysis.md so you can read it.
Cost:   ~$0.001 (cheap OpenAI model).
"""
import os
import sys
import json
from dotenv import load_dotenv
from openai import OpenAI
import costs

load_dotenv()

KEY = os.getenv("OPENAI_API_KEY")
if not KEY:
    sys.exit("ERROR: OPENAI_API_KEY is empty. Paste your OpenAI key in .env.")

MODEL = "gpt-4o-mini"
OUT_DIR = "output"

SYSTEM_PROMPT = """
You write short vertical TikTok microdramas for an audience of young lawyers.

Follow the microdrama formula:
- HOOK in the first line (grab them in 2 seconds),
- CONFLICT, then ESCALATION,
- end on a CLIFFHANGER that makes them want the next episode.

Rules:
- Use ONLY the characters you are given. Use their exact names.
- Keep it authentic. Keep legal jargon and do NOT explain it.
- Short, punchy spoken lines (a talking-head drama, not an action scene).
- 6 to 8 lines total.
- Each line also gets a very short on-screen caption (a few words).

Return ONLY valid JSON with exactly this shape:
{
  "title": "short episode title",
  "setting": "one line: where this scene happens",
  "lines": [
    {"character": "exact character name", "line": "what they say out loud", "caption": "short on-screen caption"}
  ]
}
"""


def write_script(data: dict) -> tuple[dict, float]:
    client = OpenAI(api_key=KEY)

    # Give the model the story and the cast it must use.
    cast = "\n".join(
        f"- {c['fictional_name']} ({c['role']}): {c.get('personality', '')}"
        for c in data.get("characters", [])
    )
    user_content = (
        f"STORY SUMMARY:\n{data['summary']}\n\n"
        f"WHAT HAPPENED:\n- " + "\n- ".join(data.get("what_happened", [])) + "\n\n"
        f"CHARACTERS (use only these):\n{cast}"
    )

    print(f"Writing the script with {MODEL} ...")
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,  # a bit more creative for drama
    )

    script = json.loads(resp.choices[0].message.content)
    cost = costs.openai_cost(resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return script, cost


def append_markdown(script: dict, path: str):
    """Add a readable version of the script to analysis.md."""
    lines = [f"\n\n## Script: {script.get('title', '')}",
             f"*Setting: {script.get('setting', '')}*\n"]
    for i, ln in enumerate(script.get("lines", []), 1):
        lines.append(f"{i}. **{ln['character']}:** {ln['line']}")
        lines.append(f"   _caption: {ln['caption']}_")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run the Stage 1 pipeline first.")

    with open(analysis_path) as f:
        data = json.load(f)

    script, cost = write_script(data)

    # Save the script back INTO analysis.json (everything in one place).
    data["script"] = script
    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Add a readable copy to analysis.md.
    append_markdown(script, os.path.join(OUT_DIR, "analysis.md"))

    # Show what we got.
    print(f"\nTitle: {script.get('title', '')}")
    print(f"Setting: {script.get('setting', '')}")
    print(f"Lines: {len(script.get('lines', []))}\n")
    for ln in script.get("lines", []):
        print(f"  {ln['character']}: {ln['line']}")

    print("\nSaved script -> output/analysis.json (and analysis.md)")
    costs.show("OpenAI script", cost)
