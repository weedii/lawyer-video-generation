# Lawyer Microdrama Generator

Turn real legal gossip stories into short, vertical **TikTok-style microdramas**
aimed at young, high-paying lawyers — then use those accounts to sell ads to
legal tech companies.

Stories are sourced from legal gossip sites (e.g. RollOnFriday), then
**fictionalized** but kept authentic: real names are removed, legal jargon is
kept (the jargon is the hook for the target audience). Visual style follows
shows like *Suits*, *Billions*, and *The Good Wife*.

---

## What works today: Link → Characters

Give it a story link and it automatically:
1. **Scrapes** the story (title, text, comments)
2. **Organizes** it with a cheap AI model and **invents fictional characters**
3. **Generates one image per character**

then stops. Cost: **about 5 cents per story.**

### Run it
```bash
.venv/bin/python run.py "https://www.rollonfriday.com/news-content/some-story"
```

`run.py` is the manager. It runs three steps in order:

| Step | Script | What it does | Model | Cost |
|------|--------|--------------|-------|------|
| 1 | `scrape.py <url>` | Download story + comments | — | free |
| 2 | `analyze.py` | Organize + invent fictional characters | OpenAI gpt-4o-mini | ~$0.001 |
| 3 | `gen_characters.py` | One vertical image per character | fal.ai FLUX dev | $0.025 each |

### Results (in `output/`)
- `analysis.md` — easy-to-read summary + characters
- `analysis.json` — everything together (story, characters, image file names)
- `char_*.png` — one image per character

### How names are kept fictional (reliably)
`analyze.py` uses a guardrail instead of just "asking nicely":
1. First it lists **every real name** in the article.
2. Then it **bans those exact words** when writing the analysis.
3. Then it **scans the output** and **retries** if any real name slipped through.

---

## Setup

You need **Python 3.10+**. All commands run from the project root.

### 1. Create a virtual environment
A virtual environment (venv) keeps this project's libraries separate from the
rest of your computer.
```bash
python3 -m venv .venv
```

### 2. Install the libraries into it
You have two options.

**Option A — no activation needed (used in this project):** call the venv's
Python directly with `.venv/bin/python`.
```bash
.venv/bin/pip install -r requirements.txt
```

**Option B — activate it first**, then `python`/`pip` use the venv automatically:
```bash
source .venv/bin/activate          # turn the venv on (do this each new terminal)
pip install -r requirements.txt
# ... run your commands ...
deactivate                         # turn the venv off when done
```

### 3. Add your API keys
Create a file named `.env` in the project root (it is ignored by git, so your
keys stay private):
```
FAL_KEY="your-fal-key"
ELEVENLABS_API_KEY="your-elevenlabs-key"
OPENAI_API_KEY="your-openai-key"
```

### 4. Run it
```bash
.venv/bin/python run.py "https://www.rollonfriday.com/news-content/some-story"
```
(or just `python run.py "..."` if you activated the venv in step 2B)

## Files
- `run.py` — manager (runs the whole link → characters stage)
- `scrape.py`, `analyze.py`, `gen_characters.py` — the three pipeline steps
- `costs.py` — price list; every script prints its cost
- `requirements.txt` — the Python libraries to install
- `.env` — API keys (ignored by git)

---

## Rules learned
- Everything is **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps a face consistent only with **tiny motion**; big action breaks it.
- Microdramas are mostly **talking close-ups + captions + music**.
- A talking video needs the **audio first** (the mouth copies the sound).
- One **locked image per character**, reused every time = consistency.

## Costs (estimates from provider pricing)
- Scrape: free
- Analyze (OpenAI gpt-4o-mini): ~$0.001 per story
- Character image (FLUX dev): $0.025 each
- Voice (ElevenLabs): billed by characters *(next stage)*
- Talking video (SadTalker): ~$0.05 *(next stage)*
- Silent vertical clip (Wan 2.2): $0.15 *(next stage)*

---

## Next stage (not built yet)
Characters → talking video: story → short script (who says what) → voices
(ElevenLabs) → talking lip-synced clips → captions + music → final vertical video.
Built fresh, one script at a time, all reading from `output/analysis.json`.

## Final vision (later)
Fully automated pipeline: scrape sources → score stories (good vs. bad) →
storyboard + drama arc → generate full video with consistent characters →
publish to TikTok accounts → sell ads to legal tech companies.
**Quality first, automation second.**
