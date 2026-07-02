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
microdrama as a **short film**: a **narrator hook**, then **cinematic scenes
where the characters act and talk TO EACH OTHER in the same room**, and a
**cliffhanger** ending. Cost: **about $12–15 per video.**

### Run it
```bash
python run.py "https://www.rollonfriday.com/news-content/some-story"
```

`run.py` is the manager. It runs seven steps in order:

| Step | Script | What it does | Model | Cost |
|------|--------|--------------|-------|------|
| 1 | `scrape.py <url>` | Download story + comments | — | free |
| 2 | `analyze.py` | Organize + invent fictional characters | OpenAI gpt-4o-mini | ~$0.001 |
| 3 | `gen_characters.py` | One locked vertical portrait per character (used as scene reference) | fal.ai Nano Banana Pro (2K) | $0.15 each |
| 4 | `scene_writer.py` | Write the SCENE script (5–7 scenes; each dialogue scene = 2 characters in one room) | OpenAI gpt-4o-mini | ~$0.001 |
| 5 | `voice_maker.py` | Clone each character's ElevenLabs voice into a reusable Kling voice_id + narrator voiceover | ElevenLabs + Kling create-voice | by characters |
| 6 | `scene_clips.py` | Per scene: compose the characters into one shot, then animate it as a talking dialogue scene in our cloned voices | Nano Banana Pro (compose) + Kling v3 (dialogue) + Seedance (narration) | ~$0.15/sec dialogue |
| 7 | `assemble.py` | Join the scenes into the final video | ffmpeg (local) | free |

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
- `scrape.py`, `analyze.py`, `gen_characters.py` — story → characters + portraits
- `scene_writer.py`, `voice_maker.py`, `scene_clips.py`, `assemble.py` — scenes → cloned voices → scene clips → video
- `talking_clips.py` — the OLD one-avatar-per-line renderer (kept for reference; not used by the scene pipeline)
- `costs.py` — price list; every script prints its cost
- `requirements.txt` — the Python libraries to install
- `.env` — API keys (ignored by git)

---

## Rules learned
- Everything is **vertical 9:16** (TikTok). Wide video stretches the character.
- The unit is a **SCENE, not a line.** Old avatar-per-line clips felt like each
  character was alone in a separate room. A real short film needs one shot with
  **both characters interacting** — so we generate scenes, not talking heads.
- **Character consistency across scenes:** keep one locked portrait per character
  (Nano Banana Pro), then feed those portraits as **references** when composing
  each scene image, so faces stay the same.
- **Voice consistency across scenes:** clone each character's ElevenLabs voice
  into a **Kling voice_id** once (create-voice), then reuse that id in every
  scene. Same voice every time, and they're our brand voices — a plain scene
  model would invent a new voice each clip.
- A dialogue scene may have **at most 2 speaking characters** (Kling's 2-voice
  limit); the scene writer enforces this.
- **ElevenLabs v3 clips the final word** — fix: append a trailing `—` so the cut
  lands on the dash, then trim the leftover silence (`voice_maker.py`).
- Nano Banana can compose a wide room **sideways** to fit 9:16 — compose the room
  **vertically** (people in front, room rising behind/above) and check it came
  out upright.

## Costs (estimates from provider pricing)
- Scrape: free
- Analyze + scene script (OpenAI gpt-4o-mini): ~$0.002 per story
- Character portrait (Nano Banana Pro, 2K): $0.15 each
- Voice (ElevenLabs v3) + one Kling voice clone per character: by characters (clone reused every scene)
- Composed scene image (Nano Banana Pro): $0.15 per scene
- Dialogue scene (Kling v3 standard, audio + cloned voices): $0.154 per second
- Narration motion (Seedance 1.5 Pro, no audio): $0.026 per second
- **Roughly $12–15 for one finished ~75s video** (real multi-actor cinema costs more)

---

## Next improvements
- Background music (drop `output/music.mp3`).
- Editing variety: reaction shots, B-roll, zoom-ins between lines.

## Final vision (later)
Fully automated pipeline: scrape sources → score stories (good vs. bad) →
storyboard + drama arc → generate full video with consistent characters →
publish to TikTok accounts → sell ads to legal tech companies.
**Quality first, automation second.**
