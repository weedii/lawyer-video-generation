"""STAGE 2 - STEP 1: Write the SCENE script for the microdrama (scene-based).

The unit is a SCENE, not a single line. Each scene is one continuous Seedance shot
where the characters ACT and talk TO EACH OTHER — like a real short film —
instead of one avatar talking to camera per line.

It reads the analyzed story + characters AND the raw scraped story, then writes
6-8 scenes with a real arc, in a first-person MEMOIR style:
  - a NARRATION hook to open — the PROTAGONIST, alone and doing something in a
    fitting place, tells us in first person how it began (looking at camera),
  - DIALOGUE scenes that dramatize the REAL events (two people in one room, never
    facing the camera),
  - optional NARRATION "bridge" beats (the protagonist again) between them,
  - a NARRATION cliffhanger to close.
The protagonist is BOTH narrator and actor: every narration beat is the same lead
performing to camera, so downstream it uses that character's face + cloned voice.

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

# GPT-4.1 (not the cheap mini): writes far better dramatic dialogue AND obeys the
# strict rules — use ONLY the exact cast names, keep the jargon, the structure,
# the 2-speaker cap. gpt-4o-mini invented names that weren't in the cast, which
# left characters with no voice (the "one voice says everything" bug).
MODEL = "gpt-4.1"
OUT_DIR = "output"


def lead_name(data: dict) -> str:
    """The PROTAGONIST = the character who narrates the whole story in first
    person AND acts in it (memoir style). We pick the first NAMED character
    (falling back to the very first character); the narration beats are all
    performed by this one person, so the video has one consistent narrator."""
    chars = data.get("characters", [])
    for c in chars:
        if not c.get("anonymous"):
            return c["fictional_name"]
    return chars[0]["fictional_name"] if chars else ""

# How many people a dialogue scene may hold. These are NOT model limits — the old
# 2-speaker cap came from Kling, which only accepted two voices per shot, and we no
# longer use Kling. What actually binds now:
#   SPEAKERS: the whole scene is ONE ~8s Seedance clip, and each line needs its own time
#   window to stay lip-synced and un-garbled, so only ~3 short lines fit.
#   ONSCREEN: every visible person must be composed from their own locked portrait,
#   and the more faces we pack into one vertical frame, the smaller and less stable
#   each face gets. Four is where identity still holds.
MAX_SPEAKERS = 3
MAX_ONSCREEN = 4
SYSTEM_PROMPT = """
You write short vertical TikTok microdramas for an audience of young lawyers,
based on a REAL legal news story. Write it as a first-person MEMOIR SHORT FILM.
ONE lead character — the PROTAGONIST — narrates the WHOLE story in voiceover
(first person), and that voiceover plays over EVERY scene. We SEE the other
characters act and confront each other in real rooms like a short film, but we do
NOT hear their dialogue — the protagonist's voiceover is the only voice the viewer
hears (think Goodfellas / prestige-drama narration). The on-screen characters still
act and mouth their lines so the scene feels alive, but their words are never heard.
This is NOT talking-head avatars, and it is NOT people talking to camera.

Speakers:
- Use ONLY the given characters for dialogue. Write each "character" field EXACTLY
  as the name given in the cast — copy the name only, never add extra words.
- Some characters are marked "anonymous" inside their description — real people the
  article does not name (e.g. a junior colleague). They are FULL actors with a real
  face, shown by their role; use them as speakers freely, exactly like the named
  ones.
- Use as MANY of the cast as the story needs across the scenes — there is NO limit
  on how many characters appear in the video (a story may be one lead plus several
  others). Per scene the only limits are AT MOST THREE speakers and AT MOST FOUR
  people on screen; the whole cast is never capped.
- PREFER to bring the other cast members INTO the drama, not just the two leads:
  when the story supports it, give a side character (e.g. the coroner, a colleague,
  the complainant) their own confrontation scene with one of the leads, so the
  video feels like a real world with several people — not two people talking the
  whole time. BUT let the STORY decide: only use a character when they genuinely
  belong in that beat. Never force in a character, and never invent filler dialogue
  just to use someone. A tight two-person story is fine if that is what the story
  is; a richer story should spread across more of the cast.
  CRUCIAL: each extra character gets their OWN separate scene / their OWN moment —
  NEVER swap a different person into the MIDDLE of another character's continuous
  conversation. If the lead is talking to colleague A in a place, that WHOLE
  exchange stays lead + A; colleague B is a DIFFERENT scene, not a mid-conversation
  substitution. Spreading the cast means MORE separate scenes, never a partner swap
  inside one event.
THE NARRATOR IS THE MAIN CHARACTER (first-person memoir — this is the style):
- The whole story is told by ONE lead character, the PROTAGONIST, who is BOTH the
  narrator AND an actor in the drama. The narration is NOT a detached documentary
  voice — it is the protagonist looking back and telling us THEIR OWN story in the
  FIRST PERSON ("I built that firm from nothing", "I should have seen it coming",
  "That was the morning it all fell apart").
- Every NARRATION scene is the protagonist ALONE in a place that fits where the story
  has taken them (a holding cell, the empty tribunal room, the courthouse steps,
  chambers late at night, walking a corridor), DOING something small and real (pacing,
  sitting on the bunk, staring out a window, walking slowly). We HEAR their first-person
  voiceover over the shot — but they do NOT speak aloud on camera: mouth closed, lost in
  thought. It is their inner voice, not a to-camera speech. They may glance toward us,
  but they are never talking to the lens.
- The protagonist for THIS video is named in the user message ("NARRATOR: ..."). Use
  that EXACT name. Write every narration beat in their first-person voice, and put
  that one name in the narration scene's "characters" (a one-person list).
- The DIALOGUE scenes show the characters act and mouth their lines TO EACH OTHER and
  NEVER look at the camera — but those words are NOT heard. The protagonist's first-person
  voiceover plays over the scene and is the only voice we hear.

VOICEOVER CARRIES EVERY SCENE (this is the core of the format):
- EVERY scene — narration AND dialogue — has a "narration" field: the protagonist's
  first-person voiceover that plays over it. It is the ONLY audio the viewer hears.
- For a DIALOGUE scene, the voiceover tells us what this confrontation was or what it
  meant, in the protagonist's own voice — NOT a transcript of the dialogue. E.g. over a
  shot of two colleagues arguing on a train: "She was the only one who ever called me
  out. I should have listened." It must NOT repeat the lines, and NOT describe the
  picture — it adds meaning, judgement, hindsight, or a jump in time.
- Still write the on-screen "dialogue" for the dialogue scenes: it is what the actors
  silently mouth so the scene feels alive. Keep it short and real, but remember the
  viewer never hears it — the voiceover does the storytelling.

INTRODUCE NEW FACES (so the viewer is never confused):
- PRE-NAME every important character BEFORE their face first appears. In the scene (or
  narration) that comes just before their entrance, have someone say their name and who
  they are to the protagonist — "The partner who signed my paycheck wanted a word." By
  the time we see them, the viewer already knows who they are, so their entrance lands
  instead of puzzling. (This is how the best films do it — a new face is talked about
  before it is shown.) The pre-name and the entrance must be in DIFFERENT scenes.
- The FIRST time a character appears, the scene must ALSO make their role obvious in its
  first line or action — belt and braces on top of the pre-name. Never drop a brand-new
  face into a scene with no context.

STRUCTURE (a real beginning, middle and end) — 6 to 8 SCENES total. Let the STORY
decide how many: a simple story is a tight 6, a rich one with several events earns 8.
Never pad with a scene the story doesn't need, and never starve a real event to save a
scene — cover every beat the story genuinely has.
1. OPEN with exactly one NARRATION scene (beat "intro"): the protagonist, alone in a
   fitting place and doing something, tells us in FIRST PERSON how it began — a
   punchy hook that sets up the scandal.
2. MIDDLE: 4 to 5 DIALOGUE scenes that DRAMATIZE THE REAL EVENTS, building through
   the beats "setup" -> "escalation" -> "twist". Give ONE scene per DISTINCT EVENT
   or place (e.g. the train exchange = ONE scene, the messages fallout = ONE scene,
   the tribunal = ONE scene) — do NOT split a single event into several scenes just
   to make more of them; fewer, fuller scenes look far more consistent than many
   tiny fragments that reset the set. Each scene is two characters confronting each
   other in one place. Between them you MAY put ONE short NARRATION scene (beat
   "bridge") — again the protagonist alone, first person, doing something — to bridge
   to what comes next. INSERT A BRIDGE WHENEVER THE NEXT DIALOGUE SCENE JUMPS TO A NEW
   PLACE OR BRINGS IN SOMEONE WE HAVEN'T MET, so the viewer is never dropped into a new
   room or in front of a stranger cold. The bridge must ORIENT us: in the narrator's
   own memoir voice, name (or clearly imply) the new place we're moving to or the new
   person we're about to meet and who they are to him — e.g. "The next morning they
   dragged me in front of the partner who signed my paycheck." Do it as story, never as
   stage directions ("in the next scene", "meanwhile").
3. CLOSE with exactly one NARRATION scene (beat "cliffhanger"): the protagonist,
   alone, first person, a strong button.

HARD RULES:
- PLATFORM-SAFE CONTENT (critical — the video generator REJECTS explicit prompts
  and the clip fails to render). NEVER write sexually explicit words, graphic
  descriptions, slurs or crude anatomical language, even when the real story is
  about sexual misconduct or harassment. Convey it through IMPLICATION and reaction
  instead: euphemism ("the messages", "inappropriate remarks", "over the line"),
  an uncomfortable silence, a leer, someone recoiling. Suggestive and tense is
  fine; graphic is not. Keep every line clean enough for a mainstream feed.
- STAGE THE PEOPLE THE STORY PUTS IN THE ROOM. If a beat really has a third or fourth
  person present, put them in "onscreen" and let the shot hold them — a silent
  witness, a partner watching, a clerk taking notes. They react; they need no line.
  The ONE rule: every person in the frame must be a named cast member from the list
  above, because only they have a locked photo to compose from.
- A NARRATION scene has EXACTLY ONE person — the protagonist, ALONE. Its "shot" and
  "action" must describe ONLY the protagonist (pacing, sitting, staring, walking) in
  an EMPTY place; NEVER put another character, bystander, passer-by, background
  person or crowd in a narration shot — the street/room behind them is deserted. The
  narration text is FIRST PERSON (the protagonist's own "I"/"me"), never third-person.
- KEEP NARRATION SHORT AND PUNCHY: each narration beat is ONE or at most TWO short
  sentences (about 18 words / ~7 seconds MAX). It's a sharp hook or button, not a
  paragraph. Cut every spare word — no rambling, no lists, no repeated ideas.
- NARRATION MUST NOT DESCRIBE THE PICTURE. It says what the image CANNOT — a private
  thought, a judgement, hindsight, a jump in time, the stakes. It must never narrate
  what we already see: not "I walked into the tribunal" over a shot of him walking in
  (that is the lazy, cheap kind of voiceover). Over that same shot, say the thing the
  picture can't — "Fifteen years at the Bar, undone by a group chat." The picture shows
  the WHERE; the narration adds the meaning.
- NARRATION TONE — cold, dry, specific, a little bitter. The narrator is a disgraced
  professional looking back, NOT a poet. Ban sentimental "life-lesson" or self-help
  endings and rebirth clichés: no "the rules have changed—and so have I", no "there's
  no rewinding", no "I was never the same". Those read as cheesy. Instead land a hard,
  concrete, slightly cynical line — a fact, a specific regret, or a jab — and stop.
  Aim for a sharp true-crime / prestige-drama voice, never a greeting card.
- PLAIN, SIMPLE ENGLISH (important — it's watched while scrolling). Write so ANYONE
  understands it INSTANTLY on first listen. Use short, everyday words and short
  sentences — one idea per sentence. NO literary or fancy phrasing, no big vocabulary,
  no clever metaphors that need a second read. The ONLY hard words allowed are the real
  legal terms (tribunal, struck off, injunction, inquest) — keep those, never explain
  them, but keep every OTHER word plain. If a line needs thinking to get, rewrite it
  simpler. Read it as if said out loud to a friend.
- SPEAKERS / ON-SCREEN. A dialogue scene shows everyone the story really puts in that
  room — not only the people who talk:
    * "characters" = the SPEAKERS — AT MOST THREE. This is not a model limit: the whole
      scene renders as ONE clip of about 8 seconds, so only about three short lines fit.
    * "onscreen" = EVERYONE visible in the shot — the speakers PLUS anyone who is
      really present but silent — AT MOST FOUR people. List the speakers FIRST. Silent
      people are seen and react; they simply have no line in this beat.
    * EVERY name in "onscreen" and "characters" must be an exact cast member from the
      list above. NEVER invent a name, and NEVER write an unnamed extra ("a colleague",
      "a bystander", "a crowd") — anyone we have no photo of is rendered as a random
      invented face and wrecks consistency. If someone matters but has no cast entry,
      leave them out.
- The "shot" and "action" text may describe ANY of the people listed in "onscreen",
  and NOBODY else. Never mention or hint at a person who is not in "onscreen": no
  passer-by, no crowd, no "someone nearby".
- GET IN LATE, LEAVE EARLY. Start every scene at the MOMENT OF CONFLICT, not at the
  hello. No greetings, no "sit down", no walking-in — the first line is already the
  sharp part of the argument, and the scene ends the instant the point lands. We skip
  the dull setup entirely; that is what makes a microdrama move and removes any need to
  "establish" the scene first.
- Each dialogue scene is ONE short exchange: 2 lines, or 3 at the very most,
  alternating between the speakers. The whole scene renders as a SINGLE
  continuous video clip with a hard ceiling of about 8 seconds, so a long
  back-and-forth will not fit — keep it to one sharp exchange and let the NEXT
  scene carry whatever comes after.
- Every line is SHORT and punchy: 6 to 9 words, one breath, quotable. Every speaker
  must fit inside that one ~8-second clip, so a long line gets rushed, garbled, or
  cut off. Write sharp beats, not speeches (e.g. "Book a hotel on the firm card next
  time, Rowan."). Still land a concrete story fact — just tightly.
- Every DIALOGUE line must reference a CONCRETE fact from the story (a real event,
  place, document, ruling). No vague feelings-only lines.
- BANNED generic filler — NEVER write "trust me", "I won't abandon you", "was it
  worth it", "we can't give up", "I did what I had to". Forbidden.
- Keep the legal jargon (injunction, struck off, tribunal, inquest, rights of
  audience, etc.) and do NOT explain it.
- Set every scene in the REAL location where the drama happened.
- CONTINUITY OF ACTION: write each scene's action to CONTINUE from where the
  previous scene ended — do NOT restart the blocking. If two scenes in a row have
  the same people in the same place, and someone stood up / moved / picked up a
  file at the end of the first, the next scene ASSUMES that already happened (e.g.
  "now on her feet, she paces" — NOT "she stands up" again). Never repeat the same
  physical move in consecutive scenes; the video is one flowing story, not a loop.
- ONE EVENT = ONE SCENE, ONE PAIR, ONE PLACE. A single real conversation (e.g. the
  whole train exchange) is ONE scene with ONE partner — never chop it into two or
  three fragments, and NEVER hand the second half of the same exchange (or the other
  person's reply) to a DIFFERENT character. Merge a continuous back-and-forth into a
  single scene with the full 3-4 lines. Do not create extra scenes by splitting a
  conversation — that resets the seats/framing and swaps the person mid-talk (the
  worst consistency break).
- SAME PLACE + SAME MOMENT => SAME PAIR + IDENTICAL "setting" TEXT. If you truly need
  two consecutive scenes in the same location and moment, keep the SAME two
  characters AND write the byte-for-byte IDENTICAL "setting" string in both, so the
  film keeps the exact same room, seats and framing (the pipeline continues the shot
  from the previous one). Change the pair OR the setting wording ONLY when the story
  genuinely moves to a new place or a later time. Do NOT reword the same location
  ("train carriage, morning rush" vs "same train, a bit later") — reuse it exactly.

FOR EACH SCENE also give:
- "shot": the camera for this ONE continuous take, written as a small ARC of
  framing rather than a single static setup — how it opens, moves, and where it
  lands as the line passes between the two people (e.g. "open on a medium two-shot,
  slow push-in, then favour whoever is speaking"; "over-the-shoulder that reframes
  onto the listener on the reply"). One flowing shot that changes framing with the
  dialogue — never a frozen frame, and never a list of separate angles.
- "action": the BLOCKING — what the people in the shot physically DO. For a DIALOGUE
  scene, describe ALL the "onscreen" people and how they relate in space: the two
  speakers talking, plus any silent reactors and what they do (e.g. "the lead leans
  back grinning while the solicitor opposite crosses her arms; beside her the trainee
  keeps her eyes on her file, uneasy"). Describe interaction and eyelines so they
  read as being in the same room. Mention ONLY people listed in "onscreen". For a
  NARRATION scene,
  the lone protagonist and what they do while telling us the story (e.g. "sits on
  the cell bunk, forearms on knees, looking up into the camera"; "walks the empty
  courthouse corridor toward us") — mention ONLY the protagonist.
- Every scene needs a "detail": ONE concrete object that instantly says WHERE we are —
  a brass nameplate, a gavel, a stack of tagged case files, a train window, a barred
  cell door. It must be an OBJECT, never a person, and something that genuinely belongs
  in that place. We open each new location on this detail so the viewer is oriented
  before the scene starts, so pick something legible and specific, not "a room".
- Every scene needs "time_jump": true ONLY if real time has clearly passed since the
  PREVIOUS scene — a later day, "that evening", after the hearing, weeks on. If the next
  scene continues the same stretch of time (even in a new place, like stepping off the
  train onto the platform moments later), it is false. Be sparing: most scenes are
  false. This is the only thing that earns a fade to black between scenes.
- Every scene needs "ambience": the CONTINUOUS background sound of this LOCATION that
  plays UNDER the whole scene — the sound of the PLACE, not a one-off hit. E.g. "low
  party chatter and clinking glasses", "train rumble on the rails", "hushed courtroom
  murmurs and shuffling papers", "quiet office hum with distant phones", "rain on a
  window and a ticking clock". Same location = SAME ambience wording, so scenes in one
  place share one background bed. A few words, steady and loopable, indistinct — no clear
  speech, no music.
- For DIALOGUE lines, an "emotion": the delivery cue PLUS a readable facial
  micro-expression, so the face actually performs instead of sitting flat (e.g.
  "smug, one eyebrow raised", "cold, jaw tight", "panicked, eyes darting",
  "weary, a slow blink"). A few words, no more.
- For DIALOGUE lines, also a short "reaction": how the OTHER person (the listener)
  reacts to this line WITHOUT speaking — a face-only beat we cut to (e.g. "jaw
  tightens", "looks away, stung", "a slow, disbelieving blink"). 2-5 words.

Return ONLY valid JSON with exactly this shape:
{
  "title": "short episode title",
  "setting": "one line: the real place where this drama happens",
  "scenes": [
    {"type": "narration", "beat": "intro", "setting": "a fitting place the story put the protagonist (e.g. a holding cell)", "detail": "one object that identifies this place (e.g. a barred cell door, a case bundle)", "time_jump": false, "ambience": "the continuous background sound of this place (e.g. a low cell-block hum with distant echoes)", "shot": "shot + camera on the lone protagonist", "action": "what the protagonist ALONE physically does while speaking to us (paces, sits, stares out)", "characters": ["Exact Protagonist Name"], "narration": "first-person narration the protagonist speaks to camera", "dialogue": []},
    {"type": "dialogue", "beat": "setup", "setting": "the real place", "detail": "one object that identifies this place (e.g. a brass nameplate, a gavel, a stack of case files)", "time_jump": false, "ambience": "the continuous background sound of this place (e.g. train rumble on the rails)", "shot": "shot + camera", "action": "blocking for EVERYONE onscreen: each speaker + any silent reactors and what they do", "onscreen": ["Exact Name A", "Exact Name B", "Exact Name C"], "characters": ["Exact Name A", "Exact Name B"], "narration": "the protagonist's first-person voiceover played OVER this scene — what it meant, not a transcript of the lines", "dialogue": [{"character": "Exact Name A", "line": "what is silently mouthed", "emotion": "cue", "reaction": "how B silently reacts"}, {"character": "Exact Name B", "line": "reply", "emotion": "cue", "reaction": "how A silently reacts"}]}
  ]
}
"""


def clean_scenes(script: dict, valid_names: list = None, lead: str = None) -> dict:
    """Keep only well-formed scenes and fill any missing fields, so the rest of
    the pipeline always gets clean, predictable data. Also enforce the 2-speaker
    limit per dialogue scene (the video model's hard cap).

    valid_names: the real cast names. If given, any dialogue line whose speaker is
    NOT a real cast member is DROPPED — because a made-up speaker has no voice and
    no face, so the model would speak their lines in another character's voice (the
    bug where the man said everyone's lines). This is the safety net; the real fix is
    making the model use the exact cast names in the first place."""
    valid = {n.strip().lower() for n in valid_names} if valid_names else None
    cleaned = []
    for sc in script.get("scenes", []):
        if not isinstance(sc, dict):
            continue
        stype = sc.get("type", "dialogue")
        scene = {
            "type": stype,
            "beat": sc.get("beat", ""),
            "setting": sc.get("setting", script.get("setting", "")),
            # One identifying object for the establishing detail shot (Phase 3), and
            # whether real time passed before this scene (drives the dip-to-black).
            "detail": (sc.get("detail") or "").strip(),
            "time_jump": bool(sc.get("time_jump", False)),
            # The continuous background sound of this LOCATION (party chatter, train
            # rumble). Generated once per location in scene_clips and laid UNDER the whole
            # scene by assemble, so the place is heard throughout, not just at the start.
            "ambience": (sc.get("ambience") or "").strip(),
            "shot": sc.get("shot", ""),
            "action": sc.get("action", ""),
            "characters": [],   # the SPEAKERS (<=2, get voices)
            "onscreen": [],     # EVERYONE visible in the shot (speakers + silent reactors)
            "narration": (sc.get("narration") or "").strip(),
            "dialogue": [],
        }
        if stype == "narration":
            if not scene["narration"]:
                continue  # a narration scene with no voiceover is useless
            # Narration is the protagonist performing SOLO. Force the speaker to the
            # lead so every narration beat has the same real face + cloned voice
            # (one consistent narrator), no matter what the model put here.
            if lead:
                scene["characters"] = [lead]
            scene["onscreen"] = list(scene["characters"])   # solo: only the lead
        else:
            # Keep only proper dialogue lines.
            lines = [
                {
                    "character": d.get("character", ""),
                    "line": d["line"],
                    "emotion": d.get("emotion", ""),
                    "reaction": d.get("reaction", ""),
                }
                for d in sc.get("dialogue", [])
                if isinstance(d, dict) and d.get("line") and d.get("character")
            ]
            # Drop lines spoken by a name that isn't a real cast member (no voice/face).
            if valid is not None:
                lines = [d for d in lines if d["character"].strip().lower() in valid]
            if not lines:
                continue
            # Speaking characters, in first-appearance order (see MAX_SPEAKERS).
            order = []
            for d in lines:
                if d["character"] not in order:
                    order.append(d["character"])
            speakers = order[:MAX_SPEAKERS]
            # Drop any line whose speaker isn't one of the kept speakers.
            lines = [d for d in lines if d["character"] in speakers]
            scene["characters"] = speakers
            scene["dialogue"] = lines
            # ON-SCREEN cast = everyone visible in the shot. Speakers are always in
            # (and go first, so the MAX_ONSCREEN cap can never drop a speaker); then
            # add the model's extra present people, but only REAL cast names — an
            # unknown name has no locked portrait, so composing it would invent a
            # random face. These extras are seen but silent.
            onscreen = list(speakers)
            for nm in (sc.get("onscreen") or []):
                nm = nm.strip() if isinstance(nm, str) else ""
                if nm and nm not in onscreen and (valid is None or nm.lower() in valid):
                    onscreen.append(nm)
            scene["onscreen"] = onscreen[:MAX_ONSCREEN]
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


def unknown_speakers(script: dict, valid_names: list) -> list:
    """Any speaker/character name used in the script that is NOT a real cast
    member. These are the bug: an invented name has no voice and no face, so its
    lines get spoken in another character's voice. We detect them so we can make
    the model rewrite using the exact cast names."""
    valid = {n.strip().lower() for n in valid_names}
    bad = set()
    for sc in script.get("scenes", []):
        if not isinstance(sc, dict):
            continue
        for d in sc.get("dialogue", []) or []:
            nm = (d.get("character") or "").strip() if isinstance(d, dict) else ""
            if nm and nm.lower() not in valid:
                bad.add(nm)
        for nm in sc.get("characters", []) or []:
            if nm and nm.strip().lower() not in valid:
                bad.add(nm.strip())
    return sorted(bad)


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

    # The EXACT names every "character" field must copy. Inventing any other name
    # is the bug that made one voice speak everyone's lines.
    valid_names = [c["fictional_name"] for c in data.get("characters", [])]

    # The protagonist who narrates the whole story in first person (memoir style).
    lead = lead_name(data)

    user_content = (
        f"REAL STORY (use these facts and details, but map any real people onto "
        f"the fictional cast below):\n{story_body}\n\n"
        f"SHORT SUMMARY:\n{data['summary']}\n\n"
        f"WHY IT MATTERS:\n{data.get('why_it_works', '')}\n\n"
        f"NARRATOR (the protagonist who tells the WHOLE story in first person and "
        f"performs every narration beat, alone, to camera — use this EXACT name for "
        f"every narration scene's one 'characters' entry):\n{lead}\n\n"
        f"CAST (use only these names for dialogue):\n{cast}\n\n"
        f"VALID CHARACTER NAMES — every \"character\" field MUST be copied EXACTLY "
        f"from this list, character-for-character. NEVER invent a new name or "
        f"rename anyone (not even if a name looks like a job title):\n"
        f"{' | '.join(valid_names)}"
    )

    total_cost = 0.0
    extra = ""          # banned-name correction for the next attempt
    name_extra = ""     # invented-name correction for the next attempt
    script = {}
    for attempt in range(3):
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
                {"role": "system", "content": SYSTEM_PROMPT + ban_note + name_extra},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        total_cost += costs.openai_cost(
            resp.usage.prompt_tokens, resp.usage.completion_tokens
        )
        raw = json.loads(resp.choices[0].message.content)
        unknown = unknown_speakers(raw, valid_names)          # detect BEFORE we strip them
        script = clean_scenes(raw, valid_names, lead)         # safety net: drop any that slipped

        leaks = leaked_names(script, banned)
        if not leaks and not unknown:
            break
        if unknown:
            print(f"  Used names not in the cast {unknown}; rewriting ...")
            name_extra = (
                "\n\nNAME ERROR — you used speaker names that are NOT in the cast: "
                f"{', '.join(unknown)}. Every \"character\" field must be copied "
                f"EXACTLY from this list, nothing else: {', '.join(valid_names)}."
            )
        if leaks:
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
        # The voiceover is now what the viewer HEARS on every scene, so show it first.
        who = (sc.get("characters") or ["Narrator"])[0] if sc.get("type") == "narration" else "narrator"
        if sc.get("narration"):
            lines.append(f"- _VO ({who}):_ {sc.get('narration', '')}")
        if sc.get("type") != "narration":
            onscreen = sc.get("onscreen") or sc.get("characters", [])
            speakers = set(sc.get("characters", []))
            tags = [n if n in speakers else f"{n} (silent)" for n in onscreen]
            lines.append(f"_On screen (silent, mouthed only): {', '.join(tags)}_")
            for d in sc.get("dialogue", []):
                emo = f" [{d['emotion']}]" if d.get("emotion") else ""
                lines.append(f"- _{d['character']}{emo}: {d['line']}_")
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
            extra = [n for n in sc.get("onscreen", []) if n not in sc.get("characters", [])]
            also = f"  (+ silent: {', '.join(extra)})" if extra else ""
            print(f"  Scene {i} ({sc.get('beat')}) DIALOGUE [{who}], "
                  f"{len(sc.get('dialogue', []))} lines{also}")

    print("\nSaved scene script -> output/analysis.json (and analysis.md)")
    costs.show("OpenAI scene script", cost)
