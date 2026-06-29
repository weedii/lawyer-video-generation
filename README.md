# Lawyer Microdrama Generator

Turn real legal gossip stories into short, vertical **TikTok-style microdramas**
aimed at young, high-paying lawyers — then use those accounts to sell ads to
legal tech companies.

Stories are sourced from legal gossip sites (e.g. RollOnFriday), then
**fictionalized** but kept authentic: real names are removed, legal jargon is
kept (the jargon is the hook for the target audience). Visual style follows
shows like *Suits*, *Billions*, and *The Good Wife*.

---

## What it does: Link → Final video

Give it a story link and it automatically produces a finished vertical
microdrama: a **narrator hook**, the characters acting out the scene, and a
**cliffhanger** ending. Cost: **about $3.50 per video.**

### Run it
```bash
python run.py "https://www.rollonfriday.com/news-content/some-story"
```

`run.py` is the manager. It runs seven steps in order:

| Step | Script | What it does | Model | Cost |
|------|--------|--------------|-------|------|
| 1 | `scrape.py <url>` | Download story + comments | — | free |
| 2 | `analyze.py` | Organize + invent fictional characters | OpenAI gpt-4o-mini | ~$0.001 |
| 3 | `gen_characters.py` | One cinematic vertical image per character | fal.ai Nano Banana Pro (2K) | $0.15 each |
| 4 | `scene_writer.py` | Write the scene script (who says what) | OpenAI gpt-4o-mini | ~$0.001 |
| 5 | `voice_maker.py` | One voice line per script line (narrator + characters) | ElevenLabs | by characters |
| 6 | `talking_clips.py` | Each dialogue line: character ACTS (body motion) + lip-syncs our voice; narrator beats become a moving two-character scene | Seedance 1.5 Pro + Sync lipsync (dialogue combo) + Seedance (narration) + Nano Banana Pro (scene image) | ~$0.038/sec dialogue |
| 7 | `assemble.py` | Trim each clip to its audio, then join into the final video | ffmpeg (local) | free |

### Results (in `output/`)
- `final_video.mp4` — the finished vertical microdrama
- `analysis.md` — easy-to-read story + characters + script
- `analysis.json` — everything together (story, characters, script, files)
- `char_*.png`, `voice_*.mp3`, `clip_*.mp4` — the building pieces

Optional: drop an `output/music.mp3` and the final video gets background music.

### How names are kept fictional (reliably)
`analyze.py` uses a guardrail instead of just "asking nicely":
1. First it lists **every real name** in the article.
2. Then it **bans those exact words** when writing the analysis.
3. Then it **scans the output** and **retries** if any real name slipped through.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then create a `.env` file (ignored by git) with your keys:
```
FAL_KEY="your-fal-key"
ELEVENLABS_API_KEY="your-elevenlabs-key"
OPENAI_API_KEY="your-openai-key"
```

Run it:
```bash
python run.py "https://www.rollonfriday.com/news-content/some-story"
```

## Files
- `run.py` — manager (runs the whole link → final video pipeline)
- `scrape.py`, `analyze.py`, `gen_characters.py` — story → characters + images
- `scene_writer.py`, `voice_maker.py`, `talking_clips.py`, `assemble.py` — script → voices → clips → video
- `costs.py` — price list; every script prints its cost
- `requirements.txt` — the Python libraries to install
- `.env` — API keys (ignored by git)

---

## Rules learned
- Everything is **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps a face consistent only with **tiny motion**; big action breaks it.
- Lip-sync models animate one portrait (close-up). The **big motion** (two
  people in one room, standing up) comes from a separate **scene model**
  (Seedance) used on the narration beats — that is the hybrid.
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.
- Dialogue clips use a **two-model combo**: **Seedance** animates the photo so
  the character acts with their body (stands up, leans in, gestures), then
  **Sync** lip-syncs our voice onto that moving video — real acting + correct
  lips on one clip, ~$0.038/sec (cheaper than VEED alone). VEED Fabric, Kling and
  OmniHuman remain switchable single-model options in `talking_clips.py`.
- **ElevenLabs v3 clips the final word** — fix: append a trailing `—` so the cut
  lands on the dash, then trim the leftover silence (`voice_maker.py`).
- Image models (Nano Banana Pro) can drift to landscape, so prompts force a tall
  vertical portrait **and** the scene image is re-generated if it comes out wide.

## Costs (estimates from provider pricing)
- Scrape: free
- Analyze + script (OpenAI gpt-4o-mini): ~$0.002 per story
- Character image (Nano Banana Pro, 2K): $0.15 each
- Anonymous silhouette (FLUX dev): $0.025 each
- Voice (ElevenLabs v3): billed by characters
- Dialogue clip (Seedance motion $0.026/s + Sync lipsync $0.012/s): ~$0.038 per second
- Narration motion (Seedance 1.5 Pro, no audio): $0.026 per second
- Two-character scene image (Nano Banana Pro): $0.15 once per video
- **Roughly $2.50–3.00 for one finished video**

---

## Next improvements
- Background music (drop `output/music.mp3`).
- Editing variety: reaction shots, B-roll, zoom-ins between lines.

## Final vision (later)
Fully automated pipeline: scrape sources → score stories (good vs. bad) →
storyboard + drama arc → generate full video with consistent characters →
publish to TikTok accounts → sell ads to legal tech companies.
**Quality first, automation second.**
