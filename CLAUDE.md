# CLAUDE.md — Lawyer Microdrama Project

Guidance for working in this repo. Read this first.

## How to work here (important)
- Explain in **simple English**, short, no fluff.
- Show the **exact cost of every operation** (the user asks for this every time).
- Move **step by step** and keep the path clear.
- **Verify results for real** (check video shape, audio presence, face consistency) — don't assume it worked.
- **You can only see still frames, never the moving video or its audio.** NEVER judge motion, lip-sync or "quality" yourself — describe what a frame shows and let the user watch and judge.
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

## The format — MEMOIR VOICEOVER (this is the whole approach)
The video is a first-person **memoir told by one narrator** over cinematic footage
(think Goodfellas / House of Cards voiceover):

- **ONE voice carries the whole video** — the protagonist (the lead) narrates every
  scene in the first person. That voiceover is the ONLY voice the viewer hears.
- **Characters are SEEN acting but never HEARD.** Dialogue scenes still show people
  confronting each other and silently mouthing lines, but there is **no synced dialogue
  and NO lip-sync anywhere**. The narrator tells us what happened.
- **Why:** cheap AI cannot reliably make characters talk to each other on screen. Every
  attempt broke (garbled voices with two speakers in one clip; single-face lip-sync put
  the wrong voice on the wrong face; composing separate camera angles made characters
  jump around the room). Removing on-screen dialogue removes that entire class of bugs,
  is cheaper, and is what most AI short-drama creators actually do. See **History** below.
- **Narrator voice = RANDOM per video.** Each run picks a different voice from the whole
  ElevenLabs account, matched to the lead's gender, so the channel doesn't sound like the
  same person every time (`voice_maker.py`, `NARRATOR_RANDOM`). Set `NARRATOR_RANDOM=False`
  + `NARRATOR_VOICE="<id>"` to pin one instead.
- **Plain, simple English.** The narration is written to be understood instantly while
  scrolling — short everyday words, one idea per sentence. The ONLY hard words kept are
  the real legal terms (tribunal, struck off, injunction), never explained (they filter
  for the lawyer audience).
- **Orientation is the narrator's job.** There are no establishing/detail shots (disabled,
  below). The voiceover NAMES each new place and introduces each new person as we arrive,
  and small on-screen **location cards** label the place. `scene_writer.py` enforces this
  as a hard rule so it happens on every run.

## The tools (API keys in `.env`)
- **fal.ai** — images + video.
  - **Nano Banana 2** (Google Gemini 3.1 Flash Image) — character portraits (`nano-banana-2`)
    + composing the on-screen cast into one scene image (`nano-banana-2/edit`, using the
    locked portraits so faces stay the same). **$0.08 / image at 1K**. fal charges a
    multiplier per resolution tier (512px 0.75x, **1K 1.0x = default**, 2K 1.5x, 4K 2.0x), so
    we send **no `resolution` argument** and pay the base rate — 1K is all Seedance (720p)
    and the 1080x1920 final cut can use, and 2K detail would be thrown away.
  - **Why we left non-pro** (`nano-banana`, $0.039): it shipped anatomically broken people.
    One scene came back with a **third arm** on a character (arms folded AND a second pair of
    forearms on the desk doing ANOTHER character's action, because the action text gave the
    laptop to someone the compositor had placed out of reach), and that same scene rendered a
    female character as a man. Seedance cannot fix any of that — it animates whatever still
    it is handed — so the image must be right before we pay to animate it.
  - **Bake-off, 9 real scenes, same prompts** (the earlier 7-model one — Seedream v4/v4.5,
    FLUX.2 pro/flex, Qwen — had already eliminated the cheap alternatives, which invented or
    duplicated people): **NB2 was clean on every scene**, including the third-arm scene and a
    3-person shot. **Nano Banana PRO ($0.15) fixed the anatomy but DUPLICATED a character**
    in a 2-person shot. **GPT Image 2** (OpenAI direct, ~$0.03/1K — the cheapest of the
    three) **also duplicated a character** and let the room drift from a cramped office to a
    bar. So NB2 wins on correctness, and Pro is NOT an upgrade over it.
  - **Nano Banana PRO** (`-pro`, $0.15) is kept ONLY as the **automatic compose fallback**:
    the cheap tier sometimes returns NO image on a hard 2-person shot (a `no_media_generated`
    error, not a content block — the same prompt succeeds on Pro), so `compose_scene_image`
    retries that ONE image on Pro. It is a second opinion from a different model, not a
    quality tier. You pay Pro only on the few scenes that fail, and the cost printer counts
    those fallbacks so the total stays honest.
  - **Seedance 1.5 pro i2v** (`fal-ai/bytedance/seedance/v1.5/pro/image-to-video`) — animates
    each scene image into a cinematic clip. We use the **SILENT tier only** (`generate_audio=
    False`, **$0.026/s**) — Seedance's own audio is thrown away (we don't hear the characters),
    and silent = half price + nothing to lip-sync. Seedance replaced Veo 3.1 (Veo's likeness
    filter refused our AI-invented faces on ~half of clips and cost more).
- **ElevenLabs** — two uses, both cheap:
  - **Text-to-Speech** (`eleven_multilingual_v2`) — the NARRATOR VOICEOVER. Tuned for clarity
    (`VOICE_SETTINGS`: steadier + slightly slower). $0.10 / 1k chars.
  - **Sound-Effects / sound-generation** — the per-location **ambience bed** (party chatter,
    train rumble, courtroom murmur) and one looping **music** bed. ~$0.002/s.
  - NO Speech-to-Speech, NO voice re-dub, NO lip-sync. (All retired — see History.)
- **OpenAI GPT-4.1** — story analysis + scene script. (gpt-4o-mini was too weak: it invented
  cast names, which used to break voices; GPT-4.1 obeys the cast + rules.) Pennies per video.
- **Avoid / retired:** Veo, Kling, Wan, Sync 2.0, LatentSync, Runway. See History.

---

## What is built (the working pipeline)
Run from the project root with the venv activated (`source .venv/bin/activate`), then use `python`.

One command does everything: `python run.py "<story-url>"` → `output/final_video.mp4`.
`run.py` is the runner; it runs 8 steps in order and prints each step's cost AND time,
then a total cost table and the total run time at the end.

**Re-runs are managed (`manager.py`).** Before the steps, `run.py` asks `manager.py` how to
run. If you already built this SAME link, it asks (plain English): **START OVER** (wipe +
full rebuild), **REPAIR** (keep the last run's story + narrator voice, health-scan every
artifact, delete the broken ones, and re-make ONLY the missing/broken clips + re-join —
~$0.29/clip), **SCAN** (print OK/missing/broken and stop, free), or **PICK PARTS TO REDO**
(choose specific scene(s) by number and re-make just those even if not broken — CLIP ONLY
re-animates the same still ~$0.21, or IMAGE + CLIP recomposes then animates ~$0.29). Flags
skip the prompt for automation: `--fresh`, `--repair`, `--scan`, `--redo N`, `--redo-image N`
(N can be a list like `3,6`). Repair and redo deliberately SKIP scrape/analyze/scene_writer
(AI = a different story every run) and voice_maker (a new random voice) and reuse
`output/analysis.json`. The "re-make only what's broken / picked" trick: the doctor (repair)
or the redo picker DELETES the target files, then the normal resume-guarded steps regenerate
exactly those and re-join. Redo touches ONLY the chosen files (clip = `clip_NN.mp4`, image =
`scene_NN.png`); nothing else. A wipe keeps reusable brand assets (`output/look.cube`). Last
run's summary is saved to `output/run_state.json`.

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI GPT-4.1 | `analysis.json` + `.md` | ~$0.03 |
| 3. Scene script (memoir VO) | `scene_writer.py` | OpenAI GPT-4.1 | adds `script.scenes` to analysis.json | ~$0.03–0.12 (rewrites if names leak or <7 scenes) |
| 4. Character portraits | `gen_characters.py` | Nano Banana 2 (1K) | `char_*.png` (locked refs) — only for characters the script USES | $0.08 each |
| 5. Voices | `voice_maker.py` | assigns each character a voice_id; **narrator = a RANDOM voice per video** (no audio made) | voice_id on every speaker + the narrating lead | free |
| 6. Audio | `audio_maker.py` | **ElevenLabs** — voiceover per scene (`vo_*.mp3`) + ambience bed per location (`amb_*.mp3`) + music (`music.mp3`) | audio files + `vo_file`/`vo_seconds`/`ambient` on each scene | ~$0.10/1k chars speech + ~$0.002/s sound |
| 7. Scene clips | `scene_clips.py` | Nano Banana 2 compose + **Seedance 1.5 pro SILENT**, then mux the step-6 voiceover over the silent clip | `clip_*.mp4` | ~$0.026/s Seedance + $0.08/image |
| 8. Assemble | `assemble.py` | ffmpeg (local) — joins clips, lays ambience + ducked music + location cards | `final_video.mp4` | free |

- **Audio is its OWN step now** (`audio_maker.py`, step 6): all ElevenLabs work — the per-scene
  voiceover (`vo_NN.mp3`), the per-location ambience beds (`amb_*.mp3`) and the music bed — is
  made and PAID here, in one place, so the ElevenLabs cost is printed on its own instead of
  being buried inside the clip step. Each scene gets `vo_file`/`vo_seconds`/`vo_chars` and
  `ambient` written onto it.
- **Every scene is built the same way** (`scene_clips.py`, step 7): compose ONE image of the
  on-screen cast (`nano-banana-2/edit`) → render ONE **silent** Seedance clip → mux the
  voiceover ALREADY MADE in step 6 over the silent clip (no lip-sync; the picture is trimmed to
  the voice length). So this step pays only for Seedance video + Nano Banana images. Narration
  scenes are the lone lead, contemplative, mouth closed. Dialogue scenes show the cast acting
  silently.
- **Character consistency:** one locked Nano portrait per character, reused as a reference
  into `nano-banana-2/edit` for every scene image. A per-location anchor keeps the room
  identical when the cast changes.
- Everything ends up in `output/analysis.json` (story, characters, scenes, voice_ids, files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify), and
  confusable invented names are rejected by edit-distance.
- **~$2.60 per finished ~75s video** (~15 images at $0.08 = ~$1.20, Seedance ~$1.20, sound +
  text the rest). It was ~$2 on non-pro, but non-pro shipped extra limbs and wrong genders,
  so the extra ~$0.60 buys images that are actually usable. Images and Seedance are now
  roughly even, which makes composite REUSE the biggest remaining saving. `output/music.mp3`
  (ElevenLabs) is generated automatically. No captions (location cards only).

- `manager.py` — the run brain: detect a prior run of the same link, ask start-over/repair/
  scan (plain-English prompts), health-check every artifact (ffprobe: clip has picture +
  voiceover + sane length), delete broken files so the steps rebuild them, wipe/keep the
  folder, and save `run_state.json`.
- `costs.py` — price constants + the per-step cost/time printer; every script prints its cost.
  Each paid piece is recorded under its OWN key — fal Seedance (video, step 7), fal Nano
  Banana 2 (scene images, step 7), ElevenLabs TTS (voiceover, step 6), ElevenLabs sound
  (ambience + music, step 6) — so the final "COST OF THIS VIDEO" table lists them separately
  instead of lumping them into one "clips" number. All prices web-verified (OpenAI $2/$8 per
  1M; NB2 $0.08/1K; Seedance silent $0.026/s; ElevenLabs TTS $0.10/1k, sound $0.002/s).
- **Whole-video price vs "you paid this run" (repair/redo).** `costs.record` stores TWO numbers
  per step: `amount` = the piece's real price in the finished video whether it was made this
  run or REUSED from a prior run, and `spent` = what THIS run actually paid (reused pieces = $0
  now). So the "COST OF THIS VIDEO" TOTAL always reads the same (~$4/video) no matter how many
  repairs/redos it took, and on a repair/redo an extra "YOU PAID THIS RUN" line shows just the
  few pieces regenerated (e.g. one redone scene = ~$0.31). `manager.zero_spent()` resets the
  per-run tally at the start of a repair/redo (`costs.reset_spent`), so steps that don't run
  count $0 spent while their price still stands in the total. Without this the table used to
  collapse after a redo — reused pieces recorded $0 and the printed TOTAL wrongly dropped to a
  fraction of the real cost.
- `reconcile.py` — REAL cost from the billed-units spy log (`COSTLOG=1`).
- `README.md` — the same steps in plain English.

## Rules learned (do not relearn the hard way)
- Everything must be **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks the face into a different person.
- **Fine finger motion is poison** — Seedance turns "fingers drumming on a form" into "typing
  on a keyboard" over a shot of a plain paper (the scene-6 bug). So `scene_writer.py` filters
  the `action`/`shot` text (`sanitize_motion`), deterministically, before it reaches the video
  model: **Tier 1** always strips drumming and finger-tapping; **Tier 2** strips a fine-hand
  motion (type/write/sign/scroll/count/shuffle) only in the BAD context — on a bare flat
  surface (paper/desk/table) with NO real device present ("types on a laptop" is kept, "types
  on the paper" is stripped). The prompt also lists these as banned with good/bad examples, but
  the code strip is the guarantee (never trust the model to obey). Each strip is logged.
- **The narration voiceover carries the story; characters are seen, not heard.** No synced
  dialogue, no lip-sync — that is the deliberate design, not a limitation. (We use NO
  subtitles. Short **location cards** — a place name burned briefly over a new scene, Law &
  Order style — ARE on by default; they're orientation, not captions. Toggle `LOCATION_CARDS`
  in `assemble.py`.)
- **One voice per video, chosen at random** — perfect consistency inside a video, variety
  across videos. Only the narrator (lead) is heard, so other characters' assigned voices don't
  matter.
- **Simple English** narration (keep only the legal jargon) so it's caught while scrolling.
- **Orientation without establishing shots:** the narration MUST name every new place and
  introduce every new person as we arrive (`scene_writer.py` hard rule), backed by location
  cards. This is why detail inserts could be turned off with no confusion.
- **Detail inserts are DISABLED** (`DETAIL_INSERTS=False` in `scene_clips.py`, kept in code).
  In the VO style they were a redundant 4th orientation cue (narration + cards already do it);
  off saves ~$1.40/video. Flip to `True` to bring back the face-free establishing beat.
- One **locked image per character**, reused every time = consistency.
- **Character consistency across scenes:** keep one locked portrait per character (Nano Banana
  2), then pass those portraits as **references** into `nano-banana-2/edit` when composing
  each scene image — faces stay the same.
- **Anchor the ROOM when the cast changes:** keep a per-location anchor (the first image shot
  there) and pass it into `nano-banana-2/edit` — "keep this exact room, only place the new
  people in it" (`location_ref`). Without it the model reinvents the room (a warm courtroom
  became a blue night skyscraper).
- **A shot contains ONLY its on-screen cast — nobody else.** If the script's `action`/`shot`
  names anyone not in `onscreen`, Nano invents a random face. `scene_writer.py` restricts
  action/shot to the on-screen names; `compose_scene_image` states "EXACTLY N people, no other
  face".
- **No silhouettes — every character is a real actor.** Every character (named OR
  hidden-identity) gets a real locked Nano portrait; hidden-identity people are referred to by
  role, never shown as shadows. No cap on cast size.
- Nano Banana can drift to **landscape** on wide settings or widescreen cues; force "tall
  vertical 9:16 portrait".
- **Sideways/rotated scenes:** a WIDE/horizontal layout makes Nano compose wide and rotate it
  90° to fit 9:16 (people lying sideways; pixel size stays portrait so a size check misses it).
  Fix that keeps the full room: compose **vertically using depth + height** — foreground
  people, the room rising BEHIND and ABOVE them. See `compose_scene_image`.
- Don't name real shows in the prompt or it writes them on background TVs.
- **ElevenLabs clips the final word** (e.g. "anyone" → "anyo"); append a trailing `—` so the
  cut lands on the dash, then trim the leftover silence.
- **Scene transitions — smooth, like a real short film (do NOT dip-to-black everywhere):**
  1. **Hard cut by default.** Every scene change is a straight cut.
  2. **Dip to black ONLY on a real time jump.** `scene_writer.py` sets `time_jump`; `assemble.py`
     fades only those.
  3. **Ambience J-cut carries the change.** `build_ambient_bed` starts the NEXT room's tone
     `PRELAP`≈0.6s BEFORE the picture cuts, crossfaded — the ear arrives in the new room just
     before the eye. Only the bed moves, so nothing else is touched.
  4. **Per-location ambience bed.** Each location has its own continuous background sound
     (the scene's `ambience` field — party chatter, train rumble), generated once per location
     (`make_ambient`), looped UNDER the whole scene at `BED_VOL` (currently 0.18), below the
     voice. Reused for every scene in the same place.
  5. **Continuous ducked music** + **location cards** glue and label the change. Music
     (`make_music`) runs unbroken, sidechain-DUCKED under the voice. Cards are burned via a PIL
     PNG overlay because this ffmpeg build has no `drawtext` — see `overlay_card`.
  6. **Script-side orientation:** narration names the new place/person before/at the cut; new
     characters are pre-named; "get in late, leave early"; narration says what the picture
     CAN'T (never describes the shot). `analyze.py` rejects confusable invented names via
     edit-distance.
- **NOT doing (researched, rejected):** morph/keyframe transitions (break across locations),
  whip pans (motion-sick in 9:16), wipes/glitches (stock-template look), still-image
  establishing beats (read as the video freezing).

## History — approaches we tried and dropped (do not resurrect without reason)
- **Nano Banana non-pro as the image model** ($0.039) — dropped for **Nano Banana 2** ($0.08
  at 1K). Non-pro shipped broken bodies that Seedance then animated as-is: a third arm on a
  character, and a woman rendered as a man. Also tested and rejected in the same 9-scene
  bake-off: **Nano Banana Pro** ($0.15 — fixed anatomy but duplicated a character in a
  2-person shot) and **GPT Image 2** (OpenAI direct, ~$0.03 — duplicated a character AND let
  the room drift). Cheaper is not the question; **correct cast** is.
- **An automated "image gate"** (GPT-4.1 vision checking every generated image for extra
  limbs before paying to animate it) — **built and dropped**. It reliably catches COUNT
  errors (a duplicated person, wrong cast size) but MISSED the actual third arm at full
  frame; only a zoomed crop of that region caught it, and upscaling the whole frame did
  nothing because the API downscales it anyway (identical token count). Tiling every image
  into crops would have cost ~$0.016/image. Moving to Nano Banana 2 fixed the bug at the
  source instead. Resurrect only if a model regresses and per-image verification is worth
  the tiling cost.
- **On-screen synced dialogue (the long dead end).** We tried to make 2–3 characters talk to
  each other in one shot with correct voices + matching lips. Path went Veo 3.1 → Kling v3 →
  **Seedance 1.5 pro** (native voices) → decouple with **ElevenLabs TTS + lip-sync re-dub**
  (**Sync 2.0** for 2 faces, **LatentSync** for 1) → **Wan 2.5** (audio-driven). Every variant
  failed: two voices garbled, active-speaker sync put the wrong voice on the wrong face,
  cropping to one face wrecked quality + consistency, and composing reverse angles made the
  characters jump around the room. Conclusion: cheap AI can't do reliable cinematic multi-person
  dialogue — so we switched to the memoir voiceover above. The old code (`lipsync`,
  `build_line_audio`, Sync/LatentSync constants, `build_scene_prompt`) is left in place but
  unused.
- **ElevenLabs Speech-to-Speech re-voice** (swap the narrator's clip audio into a fixed voice)
  — replaced by plain TTS voiceover (we never hear the character audio now).
- **One-shot sound effect per scene** — fired once loudly at the clip start then vanished.
  Replaced by the continuous per-location ambience bed.
- **Fixed brand narrator voice** — replaced by a random voice per video (by request).

## Current status
- **Memoir voiceover pipeline.** `run.py` takes a link → a ~75s vertical video where one
  narrator tells the story over cinematic silent footage; characters are seen acting, never
  heard; no lip-sync anywhere. ~$2.60 per video (Nano Banana 2 images at 1K). Detail inserts off.
- `scene_writer.py` writes 7–9 scenes (narration + "dialogue"-as-silent-acting) — enough to
  cover HOW the real events happened (method, the catch), aiming for a ~1–1.5 min video. The
  7-scene floor is ENFORCED in code (`MIN_SCENES`), not just asked for: GPT-4.1 ignored the
  "7-9" text and kept returning 5 (~30s videos), so a draft under 7 is now REJECTED and
  rewritten, exactly like a leaked real name — up to `MAX_ATTEMPTS` (4) tries, then a loud
  warning if it's still short. Each scene has a
  first-person voiceover, plus per-scene `ambience`, `detail`, `time_jump`. `voice_maker.py`
  gives the narrator a random gender-matched voice. `audio_maker.py` makes ALL the audio
  (voiceover + ambience + music) and prints the ElevenLabs cost on its own. `scene_clips.py`
  composes each scene image, renders ONE silent Seedance clip, and muxes the pre-made voiceover
  over it. `assemble.py` joins the clips with the ambience bed, ducked music and location cards.
- Cost tiers: Seedance 1.5 pro i2v **$0.026/s** (silent, 720p); ElevenLabs TTS **$0.10/1k
  chars**; ElevenLabs sound-generation **~$0.002/s**; Nano Banana 2 compose/portrait **$0.08/image at 1K**.
- Each script prints its **cost AND run time**; `run.py` prints the total cost table + total run time.

## Next steps (in order)
1. Judge quality on a few videos; improve weak spots (narration tone, voice fit, image quality).
2. Editing variety: reaction beats, zoom-ins, better music, maybe optional detail inserts back on.
3. Cheaper/faster: fewer Nano images (cap cast / reuse composites), parallelize the clip renders (they run one at a time now).
4. Once quality is reliably good → full automation (see below).

---

## FINAL VISION — the fully automated workflow (LATER, not now)
Once manual results are consistently good, turn the whole thing into one dynamic pipeline:

1. **Scrape** RollOnFriday + Above the Law (and other sources) for story ideas.
2. **Score** stories — automatically tell good (juicy, niche, lawyer-specific) from bad.
3. **Convert** the best stories into **storyboards and drama arcs** that follow the microdrama formula (hook → conflict → escalation → cliffhanger).
4. **Generate** the full video automatically: locked characters → scenes → voiceover → clips → ambience/music → location cards → final vertical video.
5. **Publish** to one or several TikTok accounts on a schedule.
6. **Monetize**: use the audience to sell ads to legal tech companies.

> We only build this AFTER the manual results are good enough. Quality first, automation second.
