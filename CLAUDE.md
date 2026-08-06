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
  - **Nano Banana (non-pro)** — character portraits (`nano-banana`) + composing the on-screen
    cast into one scene image (`nano-banana/edit`, using the locked portraits so faces stay
    the same). **$0.039 / image**. A 7-model bake-off (Seedream v4/v4.5, FLUX.2 pro/flex,
    Qwen) showed non-pro is the only cheaper model that keeps the EXACT cast with correct
    faces — the others invented or duplicated people. **Nano Banana PRO** (`-pro`, $0.15/2K)
    is the higher-quality tier and is used two ways: (1) an **automatic compose fallback** —
    non-pro sometimes returns NO image on a hard 2-person shot (a `no_media_generated` error,
    not a content block: the same prompt succeeds on Pro), so `compose_scene_image` retries
    that ONE image on Pro. You pay Pro only on the few scenes that fail, and the cost printer
    counts those fallbacks so the total stays honest. (2) A **manual quality flip** — set the
    MODEL constants back to the `-pro` ids if the non-pro portraits/faces ever look too soft.
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
`run.py` is the manager; it runs 7 steps in order and prints each step's cost AND time,
then a total cost table and the total run time at the end.

| Step | Script | Model | Output | Cost |
|------|--------|-------|--------|------|
| 1. Scrape story | `scrape.py <url>` | — | `output/scraped.json` | free |
| 2. Analyze + invent characters | `analyze.py` | OpenAI GPT-4.1 | `analysis.json` + `.md` | ~$0.03 |
| 3. Scene script (memoir VO) | `scene_writer.py` | OpenAI GPT-4.1 | adds `script.scenes` to analysis.json | ~$0.03 |
| 4. Character portraits | `gen_characters.py` | Nano Banana (non-pro) | `char_*.png` (locked refs) — only for characters the script USES | $0.039 each |
| 5. Voices | `voice_maker.py` | assigns each character a voice_id; **narrator = a RANDOM voice per video** | voice_id on every speaker + the narrating lead | free |
| 6. Scene clips | `scene_clips.py` | Nano Banana compose + **Seedance 1.5 pro SILENT** + ElevenLabs TTS voiceover + ambience | `clip_*.mp4` | ~$0.026/s Seedance + $0.039/image |
| 7. Assemble | `assemble.py` | ffmpeg (local) — joins clips, lays ambience + ducked music + location cards | `final_video.mp4` | free |

- **Every scene is built the same way** (`scene_clips.py`): compose ONE image of the on-screen
  cast (`nano-banana-pro/edit`) → render ONE **silent** Seedance clip → TTS the lead's
  voiceover for that scene → mux the voiceover over the silent clip (no lip-sync; the picture
  is trimmed to the voice length). Narration scenes are the lone lead, contemplative, mouth
  closed. Dialogue scenes show the cast acting silently.
- **Character consistency:** one locked Nano portrait per character, reused as a reference
  into `nano-banana-pro/edit` for every scene image. A per-location anchor keeps the room
  identical when the cast changes.
- Everything ends up in `output/analysis.json` (story, characters, scenes, voice_ids, files).
- Names are auto-fictionalized and checked (see analyze.py: find names → ban → verify), and
  confusable invented names are rejected by edit-distance.
- **~$2 per finished ~75s video** (was ~$3–5 on Nano Banana Pro; the non-pro image swap cut
  it). Seedance (silent video) and the Nano images are now roughly even. `output/music.mp3`
  (ElevenLabs) is generated automatically. No captions (location cards only).

- `costs.py` — price constants + the per-step cost/time printer; every script prints its cost.
- `reconcile.py` — REAL cost from the billed-units spy log (`COSTLOG=1`).
- `README.md` — the same steps in plain English.

## Rules learned (do not relearn the hard way)
- Everything must be **vertical 9:16** (TikTok). Wide video stretches the character.
- Cheap AI video keeps the face only with **tiny motion**. Big action breaks the face into a different person.
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
  Pro), then pass those portraits as **references** into `nano-banana-pro/edit` when composing
  each scene image — faces stay the same.
- **Anchor the ROOM when the cast changes:** keep a per-location anchor (the first image shot
  there) and pass it into `nano-banana-pro/edit` — "keep this exact room, only place the new
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
  heard; no lip-sync anywhere. ~$2 per video (Nano Banana non-pro images). Detail inserts off.
- `scene_writer.py` writes 7–9 scenes (narration + "dialogue"-as-silent-acting) — enough to
  cover HOW the real events happened (method, the catch), aiming for a ~1–1.5 min video, each with a
  first-person voiceover, plus per-scene `ambience`, `detail`, `time_jump`. `voice_maker.py`
  gives the narrator a random gender-matched voice. `scene_clips.py` composes each scene image,
  renders ONE silent Seedance clip, and muxes the lead's voiceover over it. `assemble.py` joins
  the clips with the ambience bed, ducked music and location cards.
- Cost tiers: Seedance 1.5 pro i2v **$0.026/s** (silent, 720p); ElevenLabs TTS **$0.10/1k
  chars**; ElevenLabs sound-generation **~$0.002/s**; Nano Banana non-pro compose/portrait **$0.039/image**.
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
