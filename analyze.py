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

# Text model for story analysis + inventing characters. GPT-4.1 (not the cheap
# mini): it invents better, more distinct characters and follows the rules —
# gpt-4o-mini was too dumb and kept breaking things. Still pennies per video.
MODEL = "gpt-4.1"

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
- KEEP PLACES REAL: do NOT change city, country or region names (e.g. London,
  Dubai, Birmingham). A place is the story's true SETTING, not an identity to hide,
  and faking it destroys the authenticity that is the whole hook. Only PEOPLE and the
  FIRM / company / court names get fictionalised — never the geography.

HOW MANY CHARACTERS:
- Include EVERY person who matters to the story — no maximum. A story might have
  one main character and several others, or many people. Include them all. Do
  NOT drop or merge people to hit some small number.
- Do NOT invent a separate character for pure background extras (a random judge
  on the bench, courtroom crowd, a receptionist who never matters). Those are
  just painted into the scene later; they are not characters.

HIDDEN-IDENTITY PEOPLE (people the article does not name):
- Some people in the article are NOT named — they appear only as "the women",
  "a junior colleague", "the complainant", "a witness", "Person A", or initials.
  Real legal cases hide victims/witnesses this way.
- INCLUDE these people as FULL characters — they are real actors on screen, NOT
  shadows. They get a real, detailed face and stay consistent scene to scene,
  exactly like the named characters. The ONLY difference is they are referred to
  by their ROLE, not a personal name. For them:
    * set "anonymous": true,
    * in "role", put a SHORT, clear job title (1-3 words) — e.g. "Junior
      Associate", "Trainee Solicitor", "Paralegal", "Witness", "Complainant".
      Each such role MUST be DISTINCT — never repeat the same role.
    * still write a VERY DETAILED "appearance" and a full "image_prompt" for
      them, just like everyone else, so they get a real consistent face.
    * set "gender" if the article reveals it, otherwise choose a plausible one.
    * "fictional_name" is ignored for them — the pipeline replaces it with the
      role, and the script uses that role as their name.
- A NAMED person is "anonymous": false and gets an invented name.

For EACH character (named OR hidden-identity), write rich, detailed sections,
all inferred from the story and their role (make sensible, authentic choices
where the article is silent). EVERY character must have a full "appearance",
"image_prompt", and a short "lock" — never leave them empty. The "lock" is the
identity anchor the later steps repeat word-for-word in every shot to stop the
person's face and CLOTHING from drifting, so it must name their key clothing
colour; keep it a few details, not a paragraph, and no scene/lighting words.

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
      "appearance": "VERY DETAILED physical description for image generation: age, gender, ethnicity, face shape, skin, eyes, eyebrows, nose, mouth, hair style and colour, facial hair, body build, posture, typical clothing, and any distinguishing features. Make the look fit their personality and role. REQUIRED for every character (named or hidden-identity).",
      "image_prompt": "ONE clean prompt that combines the look into a single line, cinematic Suits/Billions TV-drama style, photorealistic, professional vertical portrait. No real names. REQUIRED for every character (named or hidden-identity).",
      "lock": "ONE short identity sentence, reused unchanged as this character's anchor in every later shot: age/build, hair, THE FACE (skin tone + any beard/moustache/stubble/clean-shaven, glasses, or other standing facial feature — the video model regrows or shaves a face it is not told about, so this is not optional), KEY CLOTHING COLOUR, and one key prop (e.g. 'early-60s stocky South Asian man, salt-and-pepper hair, trimmed matching beard, charcoal suit, gold cufflinks'). Distinguishing details only — NOT a paragraph, and NO scene or lighting words."
    }
  ]
}
"""


# The SOURCE publication is where we scraped the article, not a character in it. The
# name-finder keeps returning it ("RollOnFriday", "ROF", "Above the Law"), and
# fictionalising it is meaningless — it should never appear in the drama at all. Drop it
# outright so it never lands in the ban list.
SOURCE_SITES = frozenset({
    "rollonfriday", "rof", "abovethelaw", "atl", "above the law",
    "legalcheek", "legal cheek",
})


def find_real_names(client, story: dict) -> tuple[list[str], list[str], float]:
    """Find the real names we must FICTIONALISE, returned as TWO separate lists —
    (people, organisations) — plus the cost. Keeping them apart is what lets the caller
    show a firm as a firm instead of lumping it under "names" (which made a law firm look
    like a person's name). The model sorts every proper noun into three groups so we can
    KEEP places untouched: cities and countries (London, Dubai, Birmingham) are the
    story's real SETTING, not an identity to hide, and fictionalising them would wreck
    the authenticity that is the whole hook. We ask for 'places' as its own group ONLY so
    the model stops lumping them in with names — then we discard that group. The source
    website is dropped from both lists either way."""
    text = f"{story['title']}\n{story['body']}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Read this legal news article and pull out the REAL proper nouns, "
                    "sorted into three separate groups:\n"
                    "- people: every real person's name, split into separate first-name "
                    "and surname words.\n"
                    "- organizations: the NAMED employer firms, companies, chambers, "
                    "courts or regulators only (e.g. a law firm's name).\n"
                    "- places: real cities, countries or regions (e.g. London, Dubai, "
                    "Birmingham).\n"
                    "Put each proper noun in EXACTLY ONE group. A city is a place, never "
                    "an organization. Do NOT list practice areas, departments, teams or "
                    "legal seats (e.g. 'Real Estate', 'Corporate', 'Litigation'), job "
                    "titles, or the source website the article was published on (e.g. "
                    "RollOnFriday, ROF, Above the Law) in ANY group — none of those are "
                    "names to hide. "
                    'Return ONLY JSON: {"people": ["word", ...], '
                    '"organizations": ["name", ...], "places": ["name", ...]}'
                ),
            },
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    cost = costs.openai_cost(resp.usage.prompt_tokens, resp.usage.completion_tokens)
    # PLACES are deliberately dropped so the story keeps its true geography (the
    # London/Dubai/Birmingham contrast that IS the story). The source site is stripped
    # from both groups too, in case the model listed it despite being told not to.
    def keep(items):
        return [str(n) for n in (items or []) if str(n).strip().lower() not in SOURCE_SITES]
    people = keep(data.get("people"))          # split into first/surname words by the model
    orgs = keep(data.get("organizations"))     # kept as whole firm names (e.g. "Gowling WLG")
    return people, orgs, cost


# Junk the name-finder produces when it splits ORG names and anonymised labels into
# single words: "Bar Standards Board" -> Bar/Standards/Board, "Person A" -> Person/A.
# These are ordinary words, not real personal names — but banned as names they match
# almost any sentence ("a", "bar"), so the leak-check flags a false "leak" every time
# and burns a GPT retry. We drop them and keep only distinctive name words.
NON_NAME_WORDS = frozenset({
    "a", "b", "c", "an", "the", "and", "of", "for", "to", "in", "at", "on",
    "person", "people", "bar", "standards", "board", "service", "services",
    "police", "tribunal", "tribunals", "adjudication", "authority", "council",
    "court", "courts", "chambers", "association", "society", "regulation",
    "regulatory", "commission", "office", "department", "force", "constabulary",
    "law", "legal", "firm", "company", "limited", "ltd", "llp", "group", "unit",
    "team", "panel", "committee", "solicitors", "barristers",
})


def clean_banned(names: list[str]) -> list[str]:
    """Keep only distinctive real-name words worth banning. Drop initials/short
    tokens ("A", "B") and the common institution words above, which otherwise cause
    endless false 'leaked name' retries. De-dupes too (the finder repeats words)."""
    clean, seen = [], set()
    for n in names:
        w = n.strip()
        low = w.lower()
        if len(low) <= 2 or low in NON_NAME_WORDS or not w[:1].isalpha():
            continue
        if low in seen:
            continue
        seen.add(low)
        clean.append(w)
    return clean


def label_anonymous_by_role(result: dict) -> dict:
    """Hidden-identity people are shown by their ROLE, not a personal name (they
    still get a real face — only the NAME is the role). The model fills 'role'
    (e.g. "Junior Associate") but sticks a name in 'fictional_name' anyway, so
    here we overwrite their name with the role. The rest of the pipeline uses the
    name as the character's id, and scene_writer must copy it EXACTLY — so this
    role becomes the character's one true name everywhere (kept unique)."""
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


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance — how many single-character edits turn a into b."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_collisions(result: dict) -> list[str]:
    """Find invented FIRST names that are too easy to confuse, so we can force a rename.
    Confusable names are a real source of viewer confusion ('wait, which one was that?').
    We flag a pair when their first names share the first two letters or are within two
    edits of each other. Anonymous role-labels are skipped — they aren't personal names."""
    firsts = []
    for c in result.get("characters", []):
        if c.get("anonymous"):
            continue
        fn = (c.get("fictional_name") or "").strip().split()
        if fn:
            firsts.append(fn[0])
    clashes = []
    for i in range(len(firsts)):
        for j in range(i + 1, len(firsts)):
            a, b = firsts[i], firsts[j]
            al, bl = a.lower(), b.lower()
            if al[:2] == bl[:2] or _edit_distance(al, bl) <= 2:
                clashes.append(f"{a}/{b}")
    return clashes


def analyze(story: dict) -> tuple[dict, float]:
    # timeout: don't hang forever if OpenAI is slow. max_retries: auto-retry.
    client = OpenAI(api_key=KEY, timeout=45.0, max_retries=3)
    total_cost = 0.0

    # Step 1: find the real names to fictionalise, kept as two groups (people vs firms) so
    # we can report them honestly. Firm names are split into their component words for the
    # ban list — so a partial leak of a multi-word firm ("Gowling" out of "Gowling WLG") is
    # still caught downstream — while the whole firm name is shown to the user as-is.
    # clean_banned() then strips junk (initials, generic institution words) that would
    # otherwise trigger false "leaked name" retries.
    people, orgs, c = find_real_names(client, story)
    total_cost += c
    org_words = [w for org in orgs for w in org.split()]
    banned = clean_banned(people + org_words)
    # Print the two kinds SEPARATELY, so a law firm is never displayed as if it were a
    # person's name (the old "Real names to replace: Gowling, WLG" made exactly that
    # confusing impression).
    people_show = clean_banned(people)
    print(f"Firms to fictionalize:  {', '.join(orgs) if orgs else '(none)'}")
    print("People to fictionalize: "
          f"{', '.join(people_show) if people_show else '(none — story names no real people)'}")

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
        clashes = name_collisions(result)
        if not leaks and not clashes:
            break  # clean output, we are done
        # A real name leaked, or two invented names are too alike — tell the model
        # exactly what to fix and retry. Both notes can fire on the same retry.
        extra = ""
        if leaks:
            print(f"  Leaked real names {leaks}; retrying ...")
            extra += f"\nYou previously leaked these — they are STILL banned: {', '.join(leaks)}"
        if clashes:
            print(f"  Confusable names {clashes}; retrying with distinct names ...")
            extra += ("\nThese invented names are too easy to confuse — give them clearly "
                      "DIFFERENT first names (different first letters and sounds): "
                      f"{', '.join(clashes)}")

    # Show hidden-identity people by their role ("Junior Associate") instead of a
    # personal name. This role becomes their id everywhere; scene_writer copies it
    # exactly (and the safety net there drops any name that isn't a real cast id).
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
        # Hidden-identity people have a real face too now; they are just shown by
        # role instead of a real name. Flag that, then print the full sections.
        tag = " _(shown by role)_" if c.get("anonymous") else ""
        lines.append(f"\n### {c['fictional_name']} — {c['role']}{tag}")
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
