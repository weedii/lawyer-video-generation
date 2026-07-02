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
- **fal.ai** — images + video. Models in use: **Nano Banana Pro** (character portraits + composing two characters into one scene shot via `/edit`), **Kling v3 standard i2v** (dialogue SCENES: two characters talking to each other in our cloned voices), **Kling create-voice** (clone an ElevenLabs sample → reusable voice_id), **Seedance 1.5 Pro** (narration establishing motion), **FLUX dev** (anonymous silhouettes). The old one-avatar-per-line renderer (`talking_clips.py`: OmniHuman / Seedance+Sync / VEED / Kling Avatar) is kept for reference but NOT used by the scene pipeline.
- **ElevenLabs** (v3) — voices (cloned into Kling for the scenes; narrator voiceover directly).
- **OpenAI gpt-4o-mini** — story analysis + scene script (cheap text model).
- Avoid: Runway (too expensive). We moved from one-avatar-per-line to **Kling v3 scene generation** so the characters act and talk to each other like a real short film.

---

## What is built (the working pipeline)
Run from the project root with the venv activated (`source .venv/bin/activate`), then use `python`.

One command does everything: `python run.py "<story-url>"` → `output/final_video.mp4`.
`run.py` is the manager; it runs 7 steps in order:

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI gpt-4o-mini | `analysis.json` + `.md` | ~$0.001 |
| 3. Character portraits | `gen_characters.py` | Nano Banana Pro (2K) | `char_*.png` (locked refs) | $0.15 each |
| 4. Scene script | `scene_writer.py` | OpenAI gpt-4o-mini | adds `script.scenes` to analysis.json | ~$0.001 |
| 5. Voices | `voice_maker.py` | ElevenLabs + Kling create-voice (clone) | voice_ids on chars + narrator mp3 | by characters |
| 6. Scene clips | `scene_clips.py` | Nano Banana Pro (compose 2 chars in one shot) + Kling v3 (dialogue scene in cloned voices) + Seedance (narration) | `clip_*.mp4` | ~$0.154/sec dialogue |
| 7. Assemble | `assemble.py` | ffmpeg (local) — joins the scene clips | `final_video.mp4` | free |

- **Scene-based short film:** the unit is a SCENE, not a line. Each dialogue scene = one Kling shot where **two characters act and talk TO EACH OTHER** in the same room; narrator hook/cliffhanger are Seedance establishing shots + voiceover.
- **Voice consistency:** each character's ElevenLabs voice is cloned ONCE into a Kling `voice_id` (create-voice) and reused in every scene — same voice, our brand voice.
- Everything ends up in `output/analysis.json` (story, characters, scenes, voice_ids, files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify).
- ~$12–15 per finished ~75s video. Optional `output/music.mp3` adds background music. No captions.

- `costs.py` — price constants; every script prints its cost.
- `README.md` — the same steps in plain English.

## Rules learned (do not relearn the hard way)
- Everything must be **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks the face into a different person.
- Microdramas are mostly **talking close-ups + music** — that is the right format, not a limitation. (We use NO captions.)
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.
- **The unit is a SCENE, not a line.** One-avatar-per-line always feels like each character is alone in a separate room. A real short film needs one shot with BOTH characters interacting — so we generate scenes (Kling v3 i2v), not talking heads. This was the whole point of the rebuild.
- **Character consistency across scenes:** keep one locked portrait per character (Nano Banana Pro), then pass those portraits as **references** into `nano-banana-pro/edit` when composing each scene image — faces stay the same.
- **Voice consistency across scenes:** plain scene models (Veo, Seedance, Kling) invent a NEW voice every clip. Fix: **Kling create-voice** clones an ElevenLabs sample into a reusable `voice_id`; reuse the same id in every scene (`<<<voice_1>>>`/`<<<voice_2>>>`, max 2 per shot) so the character always sounds the same — and in OUR voice.
- **ElevenLabs v3 clips the final word** (e.g. "anyone" → "anyo") even with a period. Fix in `voice_maker.py`: append a trailing `—` so the cut lands on the dash, then trim the leftover silence.
- Nano Banana Pro can drift to **landscape** on wide settings ("crowded courtroom") or widescreen cues ("film still"); force "tall vertical 9:16 portrait".
- **Sideways/rotated scenes:** asking for a WIDE/horizontal layout (two people across a desk in a room) makes Nano Banana compose wide and **rotate it 90°** to fit 9:16 — people end up lying sideways. The pixel size stays portrait, so a width/height check can't catch it. **Fix that keeps the full room:** compose the room **vertically using depth + height** — foreground people, the room rising up BEHIND and ABOVE them — so the natural composition is tall, not wide. You still get the whole environment, just stacked top-to-bottom, and it stays upright. (Do NOT "fix" it by cropping to a tight portrait — that throws away the room.) See `compose_scene_image` in `scene_clips.py`.
- Don't name real shows in the prompt or it writes them on background TVs.

## Current status
- **SCENE pipeline (this branch):** `run.py` takes a link → a short-film video where characters act and talk to each other. ~$12–15 per ~75s video. Core pieces validated by proof clips; full end-to-end run pending.
- `scene_writer.py` writes 5–7 SCENES (each dialogue scene ≤2 speaking characters). `voice_maker.py` clones each character's ElevenLabs voice into a Kling `voice_id` (create-voice) + makes narrator voiceover. `scene_clips.py` composes the characters into one shot (`nano-banana-pro/edit`) and animates it as a Kling v3 dialogue scene in the cloned voices; narration beats are Seedance motion + narrator VO. `assemble.py` joins the scenes.
- Kling v3 standard cost tiers: $0.084/s (no audio), $0.126/s (audio), **$0.154/s (audio + our cloned voices)** — the tier we use. create-voice is a one-time clone per character.
- The old one-avatar-per-line renderer lives in `talking_clips.py` (OmniHuman / Seedance+Sync / VEED / Kling Avatar) — kept for reference, not used here.

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
