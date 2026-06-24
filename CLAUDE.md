# CLAUDE.md — Lawyer Microdrama Project

Guidance for working in this repo. Read this first.

## How to work here (important)
- Explain in **simple English**, short, no fluff.
- Show the **exact cost of every operation** (the user asks for this every time).
- Move **step by step** and keep the path clear.
- **Verify results for real** (check video shape, audio presence, face consistency) — don't assume it worked.
- The per-video pipeline IS automated (`run.py`, link → final video). What is NOT built yet is the bigger auto vision: scoring sources and auto-publishing (see bottom). Focus on quality first.

---

## The idea
Make TikTok-style **microdrama series** for young, high-paying **NYC lawyers**.
Grow one or several accounts, then **sell ads to legal tech companies**.

- **Story sources:** legal gossip sites — RollOnFriday, Above the Law (more allowed). Their realness is the hook.
- **Stories:** fictionalized, but so authentic the target audience believes them.
- **Jargon:** keep it, do NOT explain it. The jargon filters for the real target audience.
- **Visual template:** Suits, Billions, The Good Wife.
- **Characters:** invented from scratch, must stay consistent across videos.

## The tools (API keys in `.env`)
- **fal.ai** — images + talking video. Models in use: **Nano Banana Pro** (character images), **FLUX dev** (establishing shots), **Kling AI Avatar v2** (talking clips).
- **ElevenLabs** — voices.
- **OpenAI gpt-4o-mini** — story analysis + scene script (cheap text model).
- Avoid: Runway (too expensive). Note: the boss disliked Kling for general video-gen, but we use Kling's separate **talking-avatar** product for lip-synced clips.

---

## What is built (the working pipeline)
Run from the project root with the venv activated (`source .venv/bin/activate`), then use `python`.

One command does everything: `python run.py "<story-url>"` → `output/final_video.mp4`.
`run.py` is the manager; it runs 7 steps in order:

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI gpt-4o-mini | `analysis.json` + `.md` | ~$0.001 |
| 3. Character images (cinematic) | `gen_characters.py` | Nano Banana Pro (2K) | `char_*.png` | $0.15 each |
| 4. Scene script | `scene_writer.py` | OpenAI gpt-4o-mini | adds `script` to analysis.json | ~$0.001 |
| 5. Voices | `voice_maker.py` | ElevenLabs | `voice_*.mp3` | by characters |
| 6. Talking clips | `talking_clips.py` | Kling AI Avatar v2 (dialogue) + FLUX dev (narrator establishing shot) | `clip_*.mp4` | $0.056/sec |
| 7. Assemble | `assemble.py` | ffmpeg (local) — trims each clip to its audio length, then joins | `final_video.mp4` | free |

- Script has a **narrator hook** (intro) + **cliffhanger** (outro); narrator lines render as establishing shots, character lines as talking clips.
- Everything ends up in `output/analysis.json` (story, characters, script, files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify).
- ~$3.50 per finished video. Optional `output/music.mp3` adds background music. No captions.

- `costs.py` — price constants; every script prints its cost.
- `README.md` — the same steps in plain English.

## Rules learned (do not relearn the hard way)
- Everything must be **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks the face into a different person.
- Microdramas are mostly **talking close-ups + music** — that is the right format, not a limitation. (We use NO captions.)
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.
- Talking-avatar models (Kling, and Kling pro) **pad short clips to a fixed ~7.2s block** longer than the audio; no parameter stops it. Fix: `assemble.py` trims every clip to its audio length. OmniHuman 1.5 matched length in tests but costs ~3×.
- Nano Banana Pro can drift to **landscape** if the prompt has widescreen cues ("film still"); force "tall vertical 9:16 portrait". Don't name real shows in the prompt or it writes them on background TVs.

## Current status
- Full pipeline automated: `run.py` takes a link → final vertical video (~$3.50). Proven end to end.
- Talking model: Kling AI Avatar v2 standard ($0.056/sec). Known tradeoff: it pads short clips a bit longer than their audio. OmniHuman 1.5 fixes that but costs ~3× more — kept Kling for cost now that Nano Banana images improved quality.

## Next steps (in order)
1. Judge quality on a few videos; improve weak spots (script tone, voice fit, lip-sync).
2. Editing variety: reaction shots, B-roll, zoom-ins, background music.
3. Once quality is reliably good → full automation (see below).

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
