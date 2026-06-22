# CLAUDE.md — Lawyer Microdrama Project

Guidance for working in this repo. Read this first.

## How to work here (important)
- Explain in **simple English**, short, no fluff.
- Show the **exact cost of every operation** (the user asks for this every time).
- Move **step by step** and keep the path clear.
- **Verify results for real** (check video shape, audio presence, face consistency) — don't assume it worked.
- We are in **manual mode**: do things by hand, get quality high first. Do NOT automate yet (the full auto vision is at the bottom).

---

## The idea
Make TikTok-style **microdrama series** for young, high-paying **NYC lawyers**.
Grow one or several accounts, then **sell ads to legal tech companies**.

- **Story sources:** legal gossip sites — RollOnFriday, Above the Law (more allowed). Their realness is the hook.
- **Stories:** fictionalized, but so authentic the target audience believes them.
- **Jargon:** keep it, do NOT explain it. The jargon filters for the real target audience.
- **Visual template:** Suits, Billions, The Good Wife.
- **Characters:** invented from scratch, must stay consistent across videos.

## The tools (boss-approved)
- **fal.ai** — images + video (API key in `.env`).
- **ElevenLabs** — voices (API key in `.env`).
- Avoid: Runway (too expensive), Kling (quality not good enough).

---

## What is built (the working pipeline)
Run from the project root using `.venv/bin/python`.

### STAGE 1 — Link → Characters (AUTOMATED)
One command: `python run.py "<story-url>"`. `run.py` is the manager; it runs:

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI gpt-4o-mini | `output/analysis.json` + `.md` | ~$0.001 |
| 3. Character images | `gen_characters.py` | FLUX dev | `output/char_*.png` | $0.025 each |

- Everything ends up in `output/analysis.json` (story, characters, image files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify).

### STAGE 2 — Characters → Talking video (NOT built yet)
To be built fresh, one script at a time, reading from `output/analysis.json`.
Planned pieces: voice (ElevenLabs), talking video (e.g. SadTalker), optional
silent motion clip (Wan 2.2). Proven settings from earlier tests are recorded
in the "Rules learned" and "Costs" sections below.

- `costs.py` — price constants; every script prints its cost.
- `README.md` — the same steps in plain English.

## Rules learned (do not relearn the hard way)
- Everything must be **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks the face into a different person.
- Microdramas are mostly **talking close-ups + captions + music** — that is the right format, not a limitation.
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.

## Current status
- STAGE 1 automated: `run.py` takes a link → scrapes → analyzes → makes character images. ~$0.05 per story.
- STAGE 2 not built yet. Earlier test scripts (gen_voice/gen_talk/gen_video) were deleted; we rebuild fresh, one script at a time, reading from analysis.json.

## Next steps (in order)
1. Build STAGE 2: story → short script (who says what) → voices → talking clips, all reading from `output/analysis.json`.
2. Add captions + music; assemble the final vertical video.
3. Judge quality. Upgrade models only where needed (show cost first).
4. Once quality is reliably good → full automation (see below).

---

## FINAL VISION — the fully automated workflow (LATER, not now)
Once manual results are consistently good, turn the whole thing into one dynamic pipeline:

1. **Scrape** RollOnFriday + Above the Law (and other sources) for story ideas.
2. **Score** stories — automatically tell good (juicy, niche, lawyer-specific) from bad.
3. **Convert** the best stories into **storyboards and drama arcs** that follow the microdrama formula (hook → conflict → escalation → cliffhanger).
4. **Generate** the full video automatically: locked characters → scenes → voices → talking clips → captions → music → final vertical video.
5. **Publish** to one or several TikTok accounts on a schedule.
6. **Monetize**: use the audience to sell ads to legal tech companies.

> We only build this AFTER the manual results are good enough. Quality first, automation second.
