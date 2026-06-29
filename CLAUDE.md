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
- **fal.ai** — images + video. Models in use: **Nano Banana Pro** (character images + two-character scene image), **FLUX dev** (anonymous silhouettes + establishing-shot fallback), **VEED Fabric 1.0** (talking lip-sync clips), **Seedance 1.5 Pro** (narration motion scenes). Kling AI Avatar v2 + OmniHuman 1.5 are kept in `talking_clips.py` as switchable talking models.
- **ElevenLabs** (v3) — voices.
- **OpenAI gpt-4o-mini** — story analysis + scene script (cheap text model).
- Avoid: Runway (too expensive). The boss disliked Kling for general video-gen; the talking lip-sync is now **VEED Fabric** (better sync, no padding).

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
| 6. Talking clips | `talking_clips.py` | COMBO: Seedance 1.5 Pro (body acting) + Sync lipsync (our voice) per dialogue line; Seedance (narration motion) + Nano Banana Pro (scene image) | `clip_*.mp4` | ~$0.038/sec dialogue |
| 7. Assemble | `assemble.py` | ffmpeg (local) — trims each clip to its audio length, then joins | `final_video.mp4` | free |

- Script has a **narrator hook** (intro) + **cliffhanger** (outro); narrator beats render as **moving two-character scenes** (Seedance), character lines as talking lip-sync clips.
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
- **Hybrid is the key trick:** lip-sync models (VEED/Kling/OmniHuman) only do close-up talking with gentle motion — they CANNOT do big actions (stand up, two people in one frame). The big motion comes from a separate **scene model (Seedance 1.5 Pro)** on the narration beats. No single model does both lip-sync-our-voice AND big motion.
- **Seedance / Veo / Kling i2v cannot lip-sync our ElevenLabs voice** — they have no input-audio field (they're silent or invent their own voice). Only avatar models (VEED, Kling Avatar, OmniHuman) lip-sync our audio.
- VEED Fabric matches the voice length (no padding). Kling pads to a fixed ~7.2s block; `assemble.py` still trims as a safety net.
- **ElevenLabs v3 clips the final word** (e.g. "anyone" → "anyo") even with a period. Fix in `voice_maker.py`: append a trailing `—` so the cut lands on the dash, then trim the leftover silence.
- Nano Banana Pro can drift to **landscape** on wide settings ("crowded courtroom") or widescreen cues ("film still"); force "tall vertical 9:16 portrait".
- **Sideways/rotated scenes:** asking for a WIDE/horizontal layout (two people across a desk in a room) makes Nano Banana compose wide and **rotate it 90°** to fit 9:16 — people end up lying sideways. The pixel size stays portrait, so a width/height check can't catch it. **Fix that keeps the full room:** compose the room **vertically using depth + height** — foreground people, the room rising up BEHIND and ABOVE them — so the natural composition is tall, not wide. You still get the whole environment, just stacked top-to-bottom, and it stays upright. (Do NOT "fix" it by cropping to a tight portrait — that throws away the room.) See `make_scene_image` in `talking_clips.py`.
- Don't name real shows in the prompt or it writes them on background TVs.

## Current status
- Full pipeline automated: `run.py` takes a link → final vertical video (~$2.50–3.00). Proven end to end.
- Dialogue clips use the **COMBO** (`TALKING_MODEL = "seedance_sync"`): **Seedance** animates the photo so the character acts with their body (stands up, leans in, gestures from the line's `action` cue), then **Sync** (`fal-ai/sync-lipsync`) lip-syncs our ElevenLabs voice onto that moving video. Real acting + correct lips on one clip, ~$0.038/sec — and cheaper than VEED alone. For lip-sync to work the Seedance prompt keeps the face toward camera.
- Switchable single-model talkers still in `talking_clips.py`: `"veed"` (VEED Fabric, lip-sync only), `"kling"`, `"omnihuman"`.
- Narration beats use **Seedance 1.5 Pro** motion scenes (two lawyers, one stands up) instead of a frozen establishing photo.

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
