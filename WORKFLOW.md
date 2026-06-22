# The Workflow (simple, step by step)

Goal: turn a legal gossip link into a microdrama. Right now the automated part
goes from a **link → characters with images**. The rest (script → voices →
talking video) is built next.

Run everything from the project root with `.venv/bin/python`.

---

## STAGE 1 — Link → Characters (AUTOMATED, done)

One command does it all:
```
.venv/bin/python run.py "https://www.rollonfriday.com/news-content/some-story"
```

`run.py` is the **manager**. It runs these in order and then stops:

1. **`scrape.py`** — downloads the story (title, text, comments) → `output/scraped.json`. Free.
2. **`analyze.py`** — sends it to a cheap OpenAI model. It hides the real names,
   invents fictional ones, writes a summary, and describes each character
   (personality + appearance + image prompt) → `output/analysis.json` + `output/analysis.md`. ~$0.001
3. **`gen_characters.py`** — makes one vertical image per character with fal.ai,
   and writes each image's file name back into `output/analysis.json`. ~$0.025 each.

**Results to look at:**
- `output/analysis.md` — read the story + characters
- `output/char_*.png` — the character images
- `output/analysis.json` — everything together (story, characters, image files)

**Cost of one full story → characters: about 5 cents.**

---

## STAGE 2 — Characters → Talking video (NEXT, not built yet)

We build this fresh, one script at a time, all reading from `output/analysis.json`.

Plan for this stage: story → short script (who says what) → voices → talking
clips → captions + music → final vertical video.

Proven settings from earlier tests (kept in the Costs/Rules below):
- Voice: ElevenLabs text-to-speech.
- Talking video: SadTalker (picture + voice → lip-synced).
- Silent motion clip: Wan 2.2 (vertical 9:16).

---

## The files
- `run.py` — the manager (runs the whole link → characters stage)
- `scrape.py`, `analyze.py`, `gen_characters.py` — the three pipeline steps
- `costs.py` — price list; every script prints its cost
- `.env` — API keys (fal.ai, ElevenLabs, OpenAI)

## Rules we learned
- Everything is **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks it.
- Microdramas are mostly **talking close-ups + captions + music**.
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.
- Names are always **fictionalized** (checked automatically), story stays authentic, jargon kept.

## Costs (estimates from provider pricing)
- Scrape: free
- Analyze (OpenAI gpt-4o-mini): ~$0.001 per story
- Character image (FLUX dev): $0.025 each
- Voice (ElevenLabs): billed by characters
- Talking video (SadTalker): ~$0.05
- Silent motion clip (Wan 2.2): $0.15
