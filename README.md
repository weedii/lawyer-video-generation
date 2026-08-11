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

Give it a story link and it automatically produces a finished vertical microdrama
in **memoir voiceover** style: **one narrator (the main character) tells the whole
story in the first person** over cinematic footage — like Goodfellas or House of
Cards. The other characters are **seen acting** in their scenes but are **never
heard**; there is no synced dialogue and no lip-sync (that's what makes it reliable
and cheap). The narrator's voiceover, small on-screen location cards, per-location
background ambience and ducked music carry it. Cost: **about $2.60 per video.**

### Run it
```bash
python run.py "https://www.rollonfriday.com/news-content/some-story"
```

**Re-running the same link.** Before it starts, `manager.py` checks whether you already
made a video from this link. If so, it asks in plain English what to do:
- **Start over** — wipe the output folder and rebuild from zero (full price).
- **Repair** — keep last time's story, characters and narrator voice, scan for clips that
  are **missing or broken**, and re-make only those, then rebuild the final video. Cheap
  (~$0.29 per fixed clip) because everything good is reused.
- **Just scan** — show what's OK / missing / broken and stop (free).
- **Pick parts to redo** — choose specific scene(s) by number and re-make just those, even
  if they aren't broken (e.g. a scene that came out wrong). For each pick you choose **clip
  only** (re-animate the same picture, ~$0.21) or **image + clip** (make a new picture too,
  ~$0.29). Everything else is untouched, then the final video is re-joined.

Skip the questions with a flag (handy for automation):
```bash
python run.py "<url>" --scan            # only check the last run's health, build nothing
python run.py "<url>" --repair          # fix missing/broken pieces only
python run.py "<url>" --fresh            # wipe and rebuild from zero
python run.py "<url>" --redo 6           # re-render just scene 6's clip (same picture)
python run.py "<url>" --redo 3,6         # several at once
python run.py "<url>" --redo-image 6     # re-make scene 6's picture AND clip
```

`run.py` is the manager. It runs eight steps in order (and prints each step's cost
and time, plus a total at the end):

| Step | Script | What it does | Model | Cost |
|------|--------|--------------|-------|------|
| 1 | `scrape.py <url>` | Download story + comments | — | free |
| 2 | `analyze.py` | Organize + invent fictional characters | OpenAI GPT-4.1 | ~$0.03 |
| 3 | `scene_writer.py` | Write the scene script (7–9 scenes covering how the events happened; a first-person voiceover over each) | OpenAI GPT-4.1 | ~$0.03–0.12* |
| 4 | `gen_characters.py` | One locked vertical portrait per USED character (scene reference) | fal.ai Nano Banana 2 (1K) | $0.08 each |
| 5 | `voice_maker.py` | Assign each character a voice (and pick the random narrator voice) — no audio made yet | ElevenLabs voice IDs | free |
| 6 | `audio_maker.py` | Make **all** the audio: the narrator voiceover per scene + one ambience bed per location + one music bed | ElevenLabs speech + sound | ~$0.10/1k chars + ~$0.002/sec |
| 7 | `scene_clips.py` | Compose each scene image, render one **silent** Seedance clip, lay the voiceover (made in step 6) over it (no lip-sync) | Nano Banana 2 (1K) + Seedance 1.5 pro (silent) | ~$0.026/sec Seedance + $0.08/image |
| 8 | `assemble.py` | Join the clips + ambience + ducked music + location cards | ffmpeg (local) | free |

\* The scene script is rewritten (another GPT call) if it leaks a real name **or comes back
with fewer than 7 scenes** — the count is enforced in code, up to 4 tries — so a messy story
can cost a bit more here. The exact per-run cost is always printed.

### Results (in `output/`)
- `final_video.mp4` — the finished vertical microdrama
- `analysis.md` — easy-to-read story + characters + script
- `analysis.json` — everything together (story, characters, script, files)
- `char_*.png`, `scene_*.png`, `clip_*.mp4`, `amb_*.mp3`, `music.mp3` — the building pieces

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
- `run.py` — runner (runs the whole link → final video pipeline; prints cost + time)
- `manager.py` — the run brain: detects a previous run of the same link, asks start-over vs.
  repair vs. scan, health-checks every artifact, and wipes/keeps the folder accordingly
- `scrape.py`, `analyze.py`, `gen_characters.py` — story → characters + portraits
- `scene_writer.py`, `voice_maker.py`, `audio_maker.py`, `scene_clips.py`, `assemble.py` — script → assign voices → make all audio → scene clips → video
- `costs.py` — price list + per-step cost/time printer (step 6 prints each paid piece —
  fal Seedance video, fal Nano Banana 2 images, ElevenLabs voice, ElevenLabs sound —
  separately, and the final cost table lists them one by one)
- `costlog.py`, `reconcile.py`, `sitecustomize.py` — cost spy: with `COSTLOG=1` set, log every API call and compute the REAL cost from actual billed units
- `requirements.txt` — the Python libraries to install
- `.env` — API keys (ignored by git)

---

## Rules learned
- Everything is **vertical 9:16** (TikTok). Wide video stretches the character.
- **Memoir voiceover, not on-screen dialogue.** One narrator tells the whole story;
  characters are seen acting but never heard. Cheap AI can't reliably lip-sync
  multi-character dialogue, so we don't try — the voiceover does the storytelling.
- **One voice per video, picked at random** from the ElevenLabs account (gender-matched
  to the lead), so the channel varies video to video. Only the narrator is heard.
- **Simple English narration**, keeping only the real legal terms (the jargon is the
  hook), so it's understood while scrolling.
- **Orientation is the narrator's job:** the voiceover names each new place and person as
  we arrive, backed by on-screen location cards — so no establishing shots are needed
  (detail inserts are disabled by default).
- **Character consistency:** one locked Nano Banana portrait per character, fed as a
  reference when composing each scene image, plus a per-location room anchor.
- **Auto Pro fallback on the scene image:** Nano Banana 2 sometimes returns no
  image on a hard two-person shot, so we retry that one image on Nano Banana Pro. Pro is a
  second opinion, not a better model — it duplicated a character in our bake-off. You only
  pay Pro's higher price on the few scenes that need it, and the printed cost counts it.
- **Per-location ambience** (party chatter, train rumble) runs continuously under each
  scene, below the voice; music runs unbroken and is ducked under the narration.
- Nano Banana can compose a wide room **sideways** to fit 9:16 — compose the room
  **vertically** (people in front, room rising behind/above) and check it came out upright.
- **ElevenLabs clips the final word** — fix: append a trailing `—` so the cut lands on the
  dash, then trim the leftover silence.

## Costs (estimates from provider pricing)
- Scrape: free
- Analyze + scene script (OpenAI GPT-4.1): ~$0.06–0.15 per story (the script is rewritten if
  it leaks a real name or comes back under 7 scenes, so a messy story costs a little more)
- Character portrait (Nano Banana 2, 1K): $0.08 each
- Composed scene image (Nano Banana 2, 1K): $0.08 per scene
- Scene clip — Seedance 1.5 pro **silent**: $0.026 per second
- Narrator voiceover (ElevenLabs TTS): $0.10 / 1,000 characters; ambience + music (sound-generation): ~$0.002/sec
- **Roughly $2.60 for one finished ~75s video** (~15 images at $0.08 = ~$1.20, Seedance ~$1.20, sound + text the rest). It was ~$2 on Nano Banana non-pro, but non-pro shipped people with extra limbs and the wrong gender, so the extra ~$0.60 buys images that are actually usable.

---

## Next improvements
- Editing variety: reaction beats, zoom-ins, better music, optional detail inserts back on.
- Cheaper/faster: fewer Nano images (cap cast, reuse composites); parallelize the clip renders. Images are now the biggest single line, so reuse pays more than it used to.

## Final vision (later)
Fully automated pipeline: scrape sources → score stories (good vs. bad) →
storyboard + drama arc → generate full video with consistent characters →
publish to TikTok accounts → sell ads to legal tech companies.
**Quality first, automation second.**
