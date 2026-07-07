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
- **fal.ai** — images + video. Models in use: **Nano Banana Pro** (character portraits + composing characters into one scene shot via `/edit`), **Veo 3.1 fast i2v** (animates each scene — two people acting + talking in ONE shot, native lip-sync — and the memoir narration). Every character gets a real locked Nano Banana portrait (no FLUX silhouettes).
- **ElevenLabs Speech-to-Speech** (`eleven_english_sts_v2`) — Veo invents its own voice per clip, so we SWAP it for each character's locked ElevenLabs voice after generation; sts keeps the timing so the lip-sync still matches. This is how we keep our own consistent voices with Veo's look. (Kling v3 / create-voice retired on this branch.)
- **OpenAI GPT-4.1** — story analysis + scene script. Upgraded from gpt-4o-mini, which was too weak: it invented character names that weren't in the cast, leaving those speakers with no voice (one voice ended up speaking everyone's lines). Still pennies per video.
- Avoid: Runway (too expensive). We moved from one-avatar-per-line to **Kling v3 scene generation** so the characters act and talk to each other like a real short film.

---

## What is built (the working pipeline)
Run from the project root with the venv activated (`source .venv/bin/activate`), then use `python`.

One command does everything: `python run.py "<story-url>"` → `output/final_video.mp4`.
`run.py` is the manager; it runs 7 steps in order:

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI GPT-4.1 | `analysis.json` + `.md` | ~$0.03 |
| 3. Scene script | `scene_writer.py` | OpenAI GPT-4.1 | adds `script.scenes` to analysis.json | ~$0.04 |
| 4. Character portraits | `gen_characters.py` | Nano Banana Pro (2K) | `char_*.png` (locked refs) — only for characters the script USES | $0.15 each |
| 5. Voices | `voice_maker.py` | assigns each character a fixed ElevenLabs voice_id (free — the swap is billed in step 6) | voice_id on every speaking char + the narrating lead | free |
| 6. Scene clips | `scene_clips.py` | Nano Banana Pro (compose chars in one shot) + Veo 3.1 fast (one clip PER LINE, native lip-sync) + ElevenLabs Speech-to-Speech (swap to our voice) | `clip_*.mp4` | ~$0.15/sec Veo |
| 7. Assemble | `assemble.py` | ffmpeg (local) — joins the scene clips | `final_video.mp4` | free |

- **Scene-based short film, memoir style:** the unit is a SCENE, not a line. Each dialogue scene = one Kling shot where **two characters act and talk TO EACH OTHER** in the same room (never to camera). The hook/bridges/cliffhanger are **NARRATION scenes: the lone LEAD performing his own story straight to camera** (first person, mouth moving, doing something in a fitting place — a cell, the tribunal, a corridor), in his own cloned voice. Same Kling model, one voice, and the ONE place a character faces the lens.
- **Voice consistency:** each character's ElevenLabs voice is cloned ONCE into a Kling `voice_id` (create-voice) and reused in every scene — same voice, our brand voice.
- Everything ends up in `output/analysis.json` (story, characters, scenes, voice_ids, files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify).
- ~$10–13 per finished ~75s video (Veo is the bulk). Optional `output/music.mp3` adds background music. No captions.

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
- **Voice consistency across scenes (Veo + swap):** Veo invents a NEW voice every clip and can't be pinned. Fix: render each clip with ONE speaker (one Veo clip per dialogue LINE), let Veo speak it (native lip-sync), then run the clip's audio through **ElevenLabs Speech-to-Speech** (`revoice`) into that character's fixed `voice_id`. sts keeps the timing, so the lip-sync still matches, and every clip comes out in our consistent per-character voice. (Validated: swapping the voice did NOT break the mouths.) One-speaker-per-clip is what makes the swap clean — a 2-speaker clip would need audio splitting.
- **Scene continuity (last-frame → straight to Kling):** do NOT reuse one static scene image across clips — that made the action REPEAT (a character stands up, then the next clip resets to sitting and she stands up again). For back-to-back dialogue clips with the SAME people in the SAME place, grab the previous clip's LAST FRAME (`extract_last_frame`, ~0.2s before the end so it's clean, keyed by `continuity_frame`) and feed it **straight to Kling as the start image** — do NOT route it through Nano `compose_scene_image`. Nano is a COMPOSER: given the portraits it rebuilds a brand-new pose/framing and throws the continuity away (verified — the composed "continuation" ignored the captured frame and reset the shot). Feeding the raw last frame to Kling is the only thing that actually continues the action; the faces in that frame are already correct (the first clip of the pair was portrait-anchored), so continuity is free ($0, saves a compose) and doesn't drift over a short run. Only the FIRST shot of a set of people (and cast/location changes) is composed by Nano (portrait-anchored, room-anchored). The SCRIPT must match: `scene_writer.py` writes each scene's action to CONTINUE from the previous one, never repeating the same move.
- **Anchor the ROOM when the cast changes:** when a character enters/leaves, we must compose a NEW scene image (new people) but the location should look identical. Fix: keep a per-location anchor (the first image shot there) and pass it into `nano-banana-pro/edit` as a reference — "keep this exact room, only place the new people in it" (`location_ref` in `scene_clips.py`). Without it the model reinvents the whole room (a warm daytime courtroom became a blue night skyscraper).
- **A shot contains ONLY its 2 characters — nobody else.** The scene image is composed from ONLY the 2 speakers' photos, so if the script's `action`/`shot` text names anyone else ("a colleague nearby", "turns to the junior solicitor"), Nano INVENTS that extra person as a random new face — wrong person, wrong gender, broken consistency. Fix both ends: `scene_writer.py` HARD RULE that action/shot mention only the 2 scene characters (a third person needs their own scene); and `compose_scene_image` states "EXACTLY N people in the frame, no other person/bystander/third face". If a third person matters, give them a separate 2-person scene.
- **No silhouettes — every character is a real actor.** The old pipeline drew unnamed people (witness, junior associate) as a FLUX backlit silhouette + role label. In the scene pipeline that breaks badly: a featureless shape can't act in a two-shot, so `nano-banana-pro/edit` either invents a random person (wrong gender) or drags the silhouette's own background into the room (the night-city silhouette is what turned a courtroom blue). Fix: every character — named OR hidden-identity — gets a real locked Nano Banana portrait; hidden-identity people are just referred to by role, not shown as shadows. **No cap on cast size** (a story can be one lead + several others); the only limit is 2 speakers per SCENE (Kling voice limit). New faces must be introduced (narrator "bridge" beat or the first line) so the viewer is never confused.
- **ElevenLabs v3 clips the final word** (e.g. "anyone" → "anyo") even with a period. Fix in `voice_maker.py`: append a trailing `—` so the cut lands on the dash, then trim the leftover silence.
- Nano Banana Pro can drift to **landscape** on wide settings ("crowded courtroom") or widescreen cues ("film still"); force "tall vertical 9:16 portrait".
- **Sideways/rotated scenes:** asking for a WIDE/horizontal layout (two people across a desk in a room) makes Nano Banana compose wide and **rotate it 90°** to fit 9:16 — people end up lying sideways. The pixel size stays portrait, so a width/height check can't catch it. **Fix that keeps the full room:** compose the room **vertically using depth + height** — foreground people, the room rising up BEHIND and ABOVE them — so the natural composition is tall, not wide. You still get the whole environment, just stacked top-to-bottom, and it stays upright. (Do NOT "fix" it by cropping to a tight portrait — that throws away the room.) See `compose_scene_image` in `scene_clips.py`.
- **Memoir narrator = the lead performs the narration, on camera, in his own voice.** The narration is NOT a detached documentary voiceover. The main character (the first named character = the protagonist) is BOTH narrator and actor: every narration beat (intro / bridges / cliffhanger) is HIM alone, telling the story in the FIRST PERSON while DOING something in a fitting place (pacing a cell, walking a corridor), mouth moving, in his OWN cloned Kling voice. It renders as a normal 1-voice Kling shot (not Seedance). **The narrator is the ONE exception to the no-camera rule** — he looks straight into the lens (House-of-Cards / memoir); dialogue scenes still never face the camera. That's why the Kling negative prompt is split: `KLING_NEG_DIALOGUE` (bans facing camera) for dialogue vs `KLING_NEG_NARRATION` (`KLING_NEG_COMMON` only, camera allowed) for narration. `scene_writer.py` forces every narration scene's single `characters` entry to the lead; `voice_maker.py` clones the lead's voice even if he never speaks in a dialogue scene; `scene_clips.py` composes him alone (`build_narration_prompt`).
- Don't name real shows in the prompt or it writes them on background TVs.
- **Kling dead lead-in:** every Kling dialogue clip opens with the characters just LOOKING at the camera for 1–6s (silent, or only breathing / a shoe scuff) before they speak. Two-part fix — attack the cause AND clean the output:
  1. **Prompt (reduce it):** `scene_clips.py` leads the positive prompt with "the scene is already in motion from the very first frame … not looking at the camera", and sets a `negative_prompt` (`KLING_NEG_DIALOGUE` = anti-camera phrases + `KLING_NEG_COMMON`: "static opening, frozen first frame, pause …" — confirmed accepted by the Kling v3 API). This only REDUCES the ease-in (all image-to-video models drift back toward the still first frame); it does not eliminate it, so we still cut.
  2. **Cut (remove the rest):** `assemble.py` `speech_onset` trims the front to where real speech starts. Key lesson: plain loudness/`silencedetect` fails — a loud breath/shoe is as loud as quiet speech, no single dB threshold separates them. What works: band-pass to the voice range (300–3400 Hz), measure energy per 0.2s window, learn THIS clip's own quiet floor, cut to the first SUSTAINED (~0.8s) speech (a single breath/scuff spike can't trigger it). Adaptive per clip, keeps a 0.25s pad so no first word is chopped, leaves narration / already-talking clips alone. Removed ~15s of staring on the first real video (56s → 41s).

## Current status
- **VEO SCENE pipeline (branch `feat/veo-scenes-with-our-voices`):** `run.py` takes a link → a short-film video where two characters act and talk to each other in one shot, in OUR voices. ~$10–13 per ~75s video. Veo two-person scene + voice-swap validated by proof clips; full end-to-end run pending.
- `scene_writer.py` writes 5–7 SCENES (unchanged: ≤2 speakers + `onscreen` cast; narration = the lead, first person, to camera). `voice_maker.py` assigns each character a fixed ElevenLabs `voice_id` (free). `scene_clips.py` composes the on-screen cast into one shot (`nano-banana-pro/edit`), then renders ONE **Veo 3.1 fast** clip per dialogue line (native lip-sync), **re-voices** each into the speaker's ElevenLabs voice (`revoice`, Speech-to-Speech), chains line clips by last-frame and concatenates them into one scene clip. Narration is one Veo clip (protagonist to camera) re-voiced. `assemble.py` joins the scenes.
- Cost tiers: Veo 3.1 fast i2v **$0.15/s** (with audio, 720p); ElevenLabs Speech-to-Speech **$0.002/s**; Nano Banana compose **$0.15/image**.
- **Upgrade path (later):** whole-scene multi-speaker Veo clips (one continuous exchange) instead of one-clip-per-line — needs audio splitting per speaker before the voice swap. Also Veo `reference-to-video` for stronger cross-scene identity.

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
