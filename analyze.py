"""STEP B: Organize the scraped story with a cheap OpenAI model.

It reads the scraped story, sends it to OpenAI, and gets back:
  - a simple-English summary (so you understand what the story is)
  - the key things that happened
  - a list of characters, each described so we can make an image of them later

Usage:
    python analyze.py

Reads:  output/scraped.json   (from scrape.py)
Output: output/analysis.json  (data for the next steps)
        output/analysis.md     (easy to read)
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

# A very cheap, good-enough text model.
# (If you have access, "gpt-4.1-nano" is even cheaper — just change this line.)
MODEL = "gpt-4o-mini"

OUT_DIR = "output"

# This tells the AI exactly what to do and exactly what shape to answer in.
# We fictionalize the people (boss's rule) but keep the story authentic.
SYSTEM_PROMPT = """
You turn real legal gossip articles into material for a fictional TikTok
microdrama aimed at young lawyers. Keep it authentic and keep legal jargon.

HARD RULE — FICTIONALIZE EVERY NAME:
- You MUST replace ALL real names with invented ones. This applies to every
  person, law firm, company, and court name that appears in the article.
- NEVER output any name that appears in the source text. Not once. Not even
  in the summary or the bullet points. If you reuse a real name, you failed.
- Change BOTH the first name AND the surname of every person. Keep NO part of
  a real name. (Example: if the real person is "Jane Smith", do not keep "Jane"
  and do not keep "Smith" — invent a fully new first name and surname.)
- The invented names must stay REALISTIC and AUTHENTIC: pick names that fit the
  same culture, gender, and background as the real person (for example, replace
  a South Asian name with a different plausible South Asian name, not a generic
  Western one). The story should still feel completely real.
- Keep everything else true to the article: the events, the legal details, the
  jargon, the drama. Only the names change.

ANONYMOUS PEOPLE (very important):
- Some people in the article are NOT named — they appear only as "the women",
  "a junior colleague", "the complainant", "a witness", "Person A", or initials.
  Real legal cases hide victims/witnesses this way.
- INCLUDE these people as characters too (they speak in the scene), but:
    * set "anonymous": true,
    * in "role", put a SHORT, clear job title (1-3 words) for their part in the
      story — e.g. "Junior Associate", "Trainee Solicitor", "Paralegal", "Legal
      Secretary", "Witness", "Complainant". We show anonymous people on screen
      BY THIS ROLE (not by a name), so each anonymous person's role MUST be
      DISTINCT — never repeat the same role.
    * do NOT describe a face for them — on screen they are a shadowy,
      unidentifiable silhouette with this role written on it.
    * set "gender" if the article reveals it (e.g. "junior female colleagues"
      -> female), otherwise "unknown".
    * leave "appearance" and "image_prompt" as empty strings "".
    * "fictional_name" is ignored for anonymous people (we replace it with the
      role), so don't worry about it.
- A NAMED person is "anonymous": false and gets the FULL treatment below
  (invented name, detailed appearance, image_prompt).

For EACH character, write rich, detailed sections, all inferred from the story
and their role (make sensible, authentic choices where the article is silent).
For anonymous characters, only "fictional_name", "role" and "gender" matter —
keep "appearance" and "image_prompt" empty.

Return ONLY valid JSON with exactly this shape:
{
  "summary": "2-3 sentences in simple English: what is this story about? (use the fictional names)",
  "what_happened": ["short bullet", "short bullet", "..."],
  "why_it_works": "1-2 sentences: why this is juicy for a lawyer audience",
  "characters": [
    {
      "fictional_name": "invented realistic name (ignored for anonymous people — they are shown by their role)",
      "role": "their role in the story (e.g. struck-off solicitor, her barrister father)",
      "anonymous": false,
      "gender": "male, female, or unknown",
      "personality": "VERY DETAILED paragraph: their character, temperament, motivations, how they behave under pressure, flaws and strengths — all justified by the story and their role.",
      "appearance": "VERY DETAILED physical description for image generation: age, gender, ethnicity, face shape, skin, eyes, eyebrows, nose, mouth, hair style and colour, facial hair, body build, posture, typical clothing, and any distinguishing features. Make the look fit their personality and role. (Empty string if anonymous.)",
      "image_prompt": "ONE clean prompt that combines the look into a single line, cinematic Suits/Billions TV-drama style, photorealistic, professional vertical portrait. No real names. (Empty string if anonymous.)"
    }
  ]
}
"""


def find_real_names(client, story: dict) -> tuple[list[str], float]:
    """Ask the model to list every real name in the article (first names and
    surnames, listed separately). We later BAN these words from the output."""
    text = f"{story['title']}\n{story['body']}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "List every real proper name in this legal article: people, "
                    "law firms, companies. Split each person's name into separate "
                    "first-name and surname words. Return ONLY JSON: "
                    '{"names": ["word", "word", ...]}'
                ),
            },
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    names = json.loads(resp.choices[0].message.content).get("names", [])
    cost = costs.openai_cost(resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return names, cost


def label_anonymous_by_role(result: dict) -> dict:
    """Anonymous people should be shown by their ROLE, not a personal name.
    The model reliably fills 'role' (e.g. "Junior Associate") but always sticks a
    name in 'fictional_name' anyway — so here we just overwrite their name with
    their role. We keep each label unique (add ' 2', ' 3' if a role repeats),
    because the rest of the pipeline uses the name as the character's id."""
    used = set()
    # Reserve the real characters' names first so an anonymous role can't clash.
    for c in result.get("characters", []):
        if not c.get("anonymous"):
            used.add(c.get("fictional_name", "").strip().lower())

    for c in result.get("characters", []):
        if not c.get("anonymous"):
            continue
        label = (c.get("role") or "Unnamed").strip()
        base, n = label, 2
        while label.lower() in used:     # make sure two roles never collide
            label = f"{base} {n}"
            n += 1
        used.add(label.lower())
        c["fictional_name"] = label      # show them by role, not a name
    return result


def leaked_names(result: dict, banned: list[str]) -> list[str]:
    """Check if any banned real name still appears anywhere in the output."""
    blob = json.dumps(result).lower()
    leaks = []
    for name in banned:
        # whole-word match, so "Bar" inside "Barrister" does not count
        if re.search(rf"\b{re.escape(name.lower())}\b", blob):
            leaks.append(name)
    return leaks


def analyze(story: dict) -> tuple[dict, float]:
    # timeout: don't hang forever if OpenAI is slow. max_retries: auto-retry.
    client = OpenAI(api_key=KEY, timeout=45.0, max_retries=3)
    total_cost = 0.0

    # Step 1: find the real names so we can ban them explicitly.
    banned, c = find_real_names(client, story)
    total_cost += c
    print(f"Real names to replace: {', '.join(banned) if banned else '(none)'}")

    # We hand the AI the raw story and comments as plain text.
    user_content = (
        f"TITLE: {story['title']}\n\n"
        f"STORY:\n{story['body']}\n\n"
        f"COMMENTS:\n- " + "\n- ".join(story["comments"])
    )

    # Step 2: generate the analysis, banning the real names. Retry once if any leak.
    extra = ""
    result = {}
    for attempt in range(2):
        ban_note = ""
        if banned:
            ban_note = (
                "\n\nBANNED NAMES — these are the REAL names. You must NOT output "
                "any of them anywhere. Replace each fully with an invented, "
                f"culturally-fitting name:\n{', '.join(banned)}{extra}"
            )
        print(f"Sending story to {MODEL} ... (attempt {attempt + 1})")
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + ban_note},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},  # force clean JSON back
            temperature=0.7,
        )
        total_cost += costs.openai_cost(
            resp.usage.prompt_tokens, resp.usage.completion_tokens
        )
        result = json.loads(resp.choices[0].message.content)

        leaks = leaked_names(result, banned)
        if not leaks:
            break  # clean output, we are done
        # A real name slipped through — tell the model exactly which, and retry.
        print(f"  Leaked real names {leaks}; retrying ...")
        extra = f"\nYou previously leaked these — they are STILL banned: {', '.join(leaks)}"

    # Show anonymous people by their role ("Junior Associate") instead of the
    # name the model insists on putting in fictional_name ("Clara Simmons").
    result = label_anonymous_by_role(result)

    # Save the list of real names we banned, so later steps (scene_writer.py)
    # can reuse the SAME ban list and never leak a real name into the script.
    result["real_names"] = banned

    # Record this step's real cost for the end-of-pipeline summary.
    costs.record(result, "analyze",
                 f"Analyze story + invent characters - OpenAI {MODEL}", total_cost)

    return result, total_cost


def write_markdown(data: dict, path: str):
    """Make a human-friendly version so you can quickly read what we got."""
    lines = [f"# Story Analysis\n", f"## Summary\n{data['summary']}\n"]

    lines.append("## What happened")
    for b in data.get("what_happened", []):
        lines.append(f"- {b}")
    lines.append("")

    lines.append(f"## Why it works\n{data.get('why_it_works', '')}\n")

    lines.append("## Characters")
    for c in data.get("characters", []):
        # Anonymous people (Person A/B) are shown on screen as a silhouette,
        # so flag them here instead of printing empty appearance fields.
        if c.get("anonymous"):
            lines.append(f"\n### {c['fictional_name']} — {c['role']} _(anonymous — silhouette)_")
            lines.append(f"\n**Personality:** {c.get('personality', '')}")
            continue
        lines.append(f"\n### {c['fictional_name']} — {c['role']}")
        lines.append(f"\n**Personality:** {c.get('personality', '')}")
        lines.append(f"\n**Appearance:** {c.get('appearance', '')}")
        lines.append(f"\n**Image prompt:** {c.get('image_prompt', '')}")
    lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    scraped_path = os.path.join(OUT_DIR, "scraped.json")
    if not os.path.exists(scraped_path):
        sys.exit("Missing output/scraped.json. Run scrape.py first.")

    with open(scraped_path) as f:
        story = json.load(f)

    data, cost = analyze(story)

    # Save the data (for next steps) and a readable version (for you).
    with open(os.path.join(OUT_DIR, "analysis.json"), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    write_markdown(data, os.path.join(OUT_DIR, "analysis.md"))

    # Show what we got.
    print(f"\nSummary: {data['summary']}")
    print(f"Characters found: {len(data.get('characters', []))}")
    for c in data.get("characters", []):
        tag = " [anonymous]" if c.get("anonymous") else ""
        print(f"  - {c['fictional_name']} ({c['role']}){tag}")

    print("\nSaved -> output/analysis.json and output/analysis.md")
    costs.show("OpenAI story analysis", cost)
