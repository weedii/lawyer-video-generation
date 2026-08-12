"""Model prices — VERIFIED from official sources, not guessed. Every script prints its
cost (and run time) using these, so we always know what we spent.

CURRENT PIPELINE (memoir voiceover) uses only:
- Nano Banana 2 (portraits + scene composites):    $0.08 / image at 1K
    fal.ai/models/fal-ai/nano-banana-2 (+ /edit)   (Google Gemini 3.1 Flash Image)
    (Nano Banana PRO, $0.15, stays as the automatic compose fallback — see the Pro
    constants below. We moved UP from non-pro ($0.039) because non-pro shipped broken
    bodies: a scene came back with a third arm on one character and another character
    rendered as the wrong gender. Nano Banana 2 fixed both, and on a 9-scene bake-off it
    also beat PRO — Pro duplicated a character in a 2-person shot, NB2 did not.)
- Seedance 1.5 Pro image-to-video, SILENT tier:    $0.026 / sec
    fal.ai/models/fal-ai/bytedance/seedance/v1.5/pro/image-to-video
- ElevenLabs Text-to-Speech (narrator voiceover):  $0.10 / 1,000 chars
- ElevenLabs sound-generation (ambience + music):  ~$0.002 / sec
- OpenAI GPT-4.1 (analysis + script):              per-token (openai_cost)
  (ElevenLabs and OpenAI bill on SEPARATE accounts, NOT fal.)

Everything else below (FLUX, Sana, VEED Fabric, Kling, OmniHuman, Sync 2.0,
LatentSync, Speech-to-Speech) is LEGACY — from approaches we tried and dropped
(on-screen dialogue + lip-sync). Kept for reference / possible reuse; not used by
the current pipeline. See CLAUDE.md "History".
"""

# Our generated images come out around 1 MP (Nano Banana 2 at its 1K tier returns e.g.
# 1376x768 landscape / 768x1376 vertical = ~1.06 MP), which fal treats as 1 MP. So for the
# per-MEGAPIXEL models further down, the listed price also equals the per-image cost.
# (Nano Banana itself is billed per IMAGE by tier, not per megapixel — see below.)
OUR_IMAGE_MEGAPIXELS = 1

# --- Image models (fal) ---------------------------------------------------
# Nano Banana 2 (Google Gemini 3.1 Flash Image) — OUR CURRENT image model. Portraits
# (gen_characters.py) and scene composites (scene_image.py) both use it.
#     fal.ai/models/fal-ai/nano-banana-2 (+ /edit)
#
# RESOLUTION TIERS (fal charges a multiplier on the 1K base price):
#     512x512  0.75x = $0.06
#     1K       1.00x = $0.08   <- DEFAULT, and what we use
#     2K       1.50x = $0.12
#     4K       2.00x = $0.16
# 1K is the DEFAULT, so we send NO "resolution" argument at all and pay the base rate.
# 1K is also all we need: the composed image only has to feed Seedance, which renders at
# 720p, and assemble.py outputs 1080x1920. Paying 1.5x for 2K would be thrown away.
NANO_BANANA_2_PER_IMAGE = 0.08        # portraits (text-to-image), 1K
NANO_BANANA_2_EDIT_PER_IMAGE = 0.08   # scene composites (compose the cast into one shot), 1K

# Nano Banana PRO — the AUTOMATIC compose fallback (scene_image.py). NB2 is tried first;
# when it returns no image on a hard multi-person shot, that ONE image is retried on Pro.
# The per-video cost adds this price only for the composes that actually fell back
# (scene_image flags each fallback, scene_clips sums them), so the printed cost is honest
# whether 0 or 3 scenes needed Pro.
# NOTE: Pro is the fallback, NOT an upgrade — on a 9-scene bake-off Pro DUPLICATED a
# character in a 2-person shot while NB2 rendered the cast correctly. It is here because a
# second opinion from a different model beats returning no image at all.
#     fal.ai/models/fal-ai/nano-banana-pro/edit
NANO_BANANA_PRO_PER_IMAGE = 0.15         # Pro portrait (1K and 2K are both $0.15; 4K is $0.30)
NANO_BANANA_PRO_EDIT_PER_IMAGE = 0.15    # Pro compose (also the auto compose-fallback price)

# LEGACY — Nano Banana non-pro (Gemini 2.5 Flash Image), the model we ran before NB2.
# Kept only so old runs / api_calls.jsonl logs still price correctly. Do not use: it was
# replaced because it shipped anatomically broken people (a third arm grafted onto a
# character, and a woman rendered as a man) that the video model then animated as-is.
#     fal.ai/models/fal-ai/nano-banana (+ /edit)
NANO_BANANA_PER_IMAGE = 0.039        # legacy portraits
NANO_BANANA_EDIT_PER_IMAGE = 0.039   # legacy scene composites

# FLUX.1 [dev]: $0.025/MP -> $0.025 per image at our size. Used for the
# establishing shot and (currently) the anonymous silhouettes.
FLUX_DEV_PER_IMAGE = 0.025

# Cheaper options for the anonymous silhouette (it's just a dark shape, so image
# quality barely matters). Cost per image at our 1-MP size:
FLUX_SCHNELL_PER_IMAGE = 0.003    # $0.003 / MP
SANA_PER_IMAGE = 0.001            # $0.001 / MP
FAST_SDXL_PER_IMAGE = 0.0023      # ~$0.00111/compute-sec x ~2s; COMPUTE-billed, varies

# --- Talking-video models (fal) -------------------------------------------
# These bill on the OUTPUT video length. We now use VEED Fabric (below) as the
# default talker; Kling and OmniHuman are kept as switchable options.
# NOTE on Kling: it PADS every clip to a fixed ~7.2s block (longer than the
# speech), so a Kling clip costs ~7.2s x $0.0562 = ~$0.40 EACH no matter how
# short the line is. VEED Fabric does NOT pad (length matches the voice), which
# is the main reason we switched. Anonymous lines stay free (static silhouette +
# voice via ffmpeg); narration beats now cost a little (Seedance motion, below).
KLING_AVATAR_PER_SEC = 0.0562
KLING_PADDED_CLIP_SECONDS = 7.2                                   # fixed block Kling outputs
KLING_COST_PER_CLIP_EST = KLING_AVATAR_PER_SEC * KLING_PADDED_CLIP_SECONDS  # ~$0.40

# OmniHuman 1.5 (alternative, not wired): $0.16/sec but length MATCHES the audio
# (no padding). Break-even vs Kling is ~2.5s of audio: shorter lines are cheaper
# on OmniHuman, longer lines are cheaper on Kling. Most lines are >2.5s, so Kling
# usually wins — which is why we keep Kling.
OMNIHUMAN_PER_SEC = 0.16

# VEED Fabric 1.0 (our talking lip-sync model): animates a still photo to our
# voice. Unlike Kling it does NOT pad — the clip length matches the voice, so we
# only pay for real seconds. 480p is the cheap tier we use; 720p is HD.
#     fal.ai/models/veed/fabric-1.0
VEED_FABRIC_480P_PER_SEC = 0.08
VEED_FABRIC_720P_PER_SEC = 0.15

# --- Scene model (fal) — the pipeline's main cost -------------------------
# Seedance 1.5 Pro image-to-video is now THE scene model: two people act + talk in
# ONE shot with native lip-sync. It replaced Veo 3.1 — Veo's likeness filter refused
# our AI-invented faces on ~half of clips (a Google policy that also cost more).
# Seedance holds the same faces, is NOT blocked, and is cheaper. It bills per second,
# and turning native audio OFF HALVES the price:
#   720p WITH audio    = $0.052/sec  <- spoken scenes (dialogue + narration)
#   720p WITHOUT audio = $0.026/sec  <- silent detail inserts
#     fal.ai/models/fal-ai/bytedance/seedance/v1.5/pro/image-to-video
SEEDANCE_PRO_AUDIO_PER_SEC = 0.052
SEEDANCE_PRO_NOAUDIO_PER_SEC = 0.026


def seedance_rates():
    """(spoken $/s, silent $/s) for Seedance 1.5 pro, so the printed cost stays honest:
    spoken scenes render WITH audio; the face-free detail inserts render WITHOUT it,
    which bills at half the rate."""
    return SEEDANCE_PRO_AUDIO_PER_SEC, SEEDANCE_PRO_NOAUDIO_PER_SEC

# --- Scene model (Kling v3) — the scene pipeline's main cost ----------------
# Kling v3 standard image-to-video: animates a composed two-character image into
# a cinematic dialogue SHOT with native voices. We pass our cloned voice_ids, so
# we're on the "voice control" tier. Prices from fal (per second of output):
#   $0.084/s  no audio
#   $0.126/s  with audio (model picks voices)
#   $0.154/s  with audio + voice control (our cloned voice_ids)  <- what we use
#     fal.ai/models/fal-ai/kling-video/v3/standard/image-to-video
KLING_V3_STD_NOAUDIO_PER_SEC = 0.084
KLING_V3_STD_AUDIO_PER_SEC = 0.126
KLING_V3_STD_VOICE_PER_SEC = 0.154

# Kling create-voice: clone one ElevenLabs sample -> a reusable voice_id. Done
# ONCE per character (reused in every scene), not per clip. VERIFIED $0.007 per
# call on the fal page, and confirmed by the response header x-fal-billable-units:1.
#     fal.ai/models/fal-ai/kling-video/create-voice
KLING_CREATE_VOICE_PER = 0.007

# Sync lipsync 1.x (fal-ai/sync-lipsync): legacy, kept for reference. $0.0117/s.
SYNC_LIPSYNC_PER_SEC = 0.0117

# --- Re-dub / lip-sync (fal) — the current dialogue+narration fix -----------
# We render Seedance WITH audio (mouths already moving), then RE-DUB the mouths onto our
# CORRECT ElevenLabs words. Two tools by face count:
#   Sync Lipsync 2.0 (fal-ai/sync-lipsync/v2) — DIALOGUE (2 faces). Its active-speaker
#     detection maps each voice to the right face. $3.00 / minute = $0.05 / output second.
#       fal.ai/models/fal-ai/sync-lipsync/v2
SYNC_LIPSYNC2_PER_SEC = 0.05
#   LatentSync (fal-ai/latentsync) — NARRATION (1 face), cheaper. Flat $0.20 for clips up to
#     40s, then $0.005/s beyond. Our narration clips are < 40s, so it's a flat $0.20 each.
#       fal.ai/models/fal-ai/latentsync
LATENTSYNC_FLAT = 0.20
LATENTSYNC_OVER_40_PER_SEC = 0.005

# ElevenLabs Text-to-Speech: makes the CORRECT words in each character's fixed voice (the
# input to the re-dub). eleven_multilingual_v2 = $0.10 / 1,000 characters. Tiny per line.
#     elevenlabs.io/docs/api-reference/text-to-speech
ELEVEN_TTS_PER_1K_CHARS = 0.10

# (Veo 3.1 fast — via fal and via Google's Gemini API — was the previous scene model.
# It was dropped for Seedance 1.5 pro above: Veo's likeness filter blocked our AI faces
# on ~half of clips and it cost more. Its rate constants + veo_rates() were removed.)

# ElevenLabs Speech-to-Speech (voice changer): swaps the model's invented voice for OUR
# locked character voice while KEEPING the timing, so the lip-sync still matches.
# $0.12 / minute of audio = $0.002 / second.
#     elevenlabs.io/docs/api-reference/speech-to-speech
ELEVEN_STS_PER_SEC = 0.002

# ElevenLabs Sound-Effects (text -> sound). The EDITOR lays one continuous,
# seamlessly-looping "ambient bed" (room tone) per location UNDER the whole scene,
# so the audio never drops to silence at a cut — the single biggest trick for
# hiding the seams between separately-generated clips. We generate ONE short loop
# (~15s) per unique location and loop it to length, so the real spend is tiny.
# Billed at $0.12 / minute of generated audio = $0.002 / second.
#     elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
ELEVEN_SFX_PER_SEC = 0.002

# --- Text + voice (SEPARATE accounts, not fal) ----------------------------
# ElevenLabs bills by characters from your plan quota, not per-call dollars.
# This is a rough upper-bound estimate just so a number prints.
ELEVENLABS_PER_1K_CHARS = 0.30

# OpenAI GPT-4.1 published rate, per 1,000,000 tokens. Still small (~$0.10-0.15/video for
# analyze + script) next to the Seedance clips. openai_cost() bills the REAL tokens, so this
# stays honest even when the scene writer rewrites the script (a leaked name, or fewer than
# MIN_SCENES scenes) — each extra attempt just adds its real tokens to the total.
# (gpt-4o-mini was 0.15/0.60 but too weak — see docs.)
OPENAI_INPUT_PER_1M = 2.00    # $ per 1,000,000 input tokens
OPENAI_OUTPUT_PER_1M = 8.00   # $ per 1,000,000 output tokens


# When this module is first imported (at the very top of every script) we stamp the
# start time. Because each script imports costs before doing any work, this is
# effectively the script's start, so show() can report how long that script took.
import time as _time
_START = _time.time()


def fmt_duration(secs: float) -> str:
    """Turn seconds into a short human string, e.g. '3m 07s' or '1h 04m 09s'."""
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def elapsed() -> float:
    """Seconds since this script started (since costs was imported)."""
    return _time.time() - _START


def show(label: str, amount: float):
    """Print a step's cost AND how long the step took (so each script, run on its own,
    reports its own run time right under its cost)."""
    print(f"\n COST: {label} = ${amount:.4f}")
    print(f" TIME: {fmt_duration(elapsed())}\n")


# --- Per-video cost summary -------------------------------------------------
# Each pipeline step records its real cost into analysis.json (under "costs"),
# keyed by step so re-running a step OVERWRITES its entry instead of double
# counting. run.py reads it at the end and prints the breakdown + grand total.
# One key per PAID thing, so the final table lists each on its own line (never lumped):
# audio_maker (step 6) records tts + sound (ElevenLabs); scene_clips (step 7) records
# scene_video + scene_images (fal). The old combined "clips" row is gone.
_SUMMARY_ORDER = ["analyze", "script", "images", "voices",
                  "tts", "sound",                          # step 6: audio (ElevenLabs)
                  "scene_images", "scene_video", "detail_video",   # step 7: video (fal)
                  "clips"]   # "clips" = legacy combined key from old runs (still printed if present)


def record(data: dict, key: str, label: str, amount: float, spent: float = None):
    """Save one step's cost into the analysis data, so the whole-video total can be printed
    when the pipeline finishes.

    Two numbers are stored per step:
      - amount = the TRUE price of this step's pieces in the finished video, whether they were
        made this run or REUSED from a previous run. This is what the video really cost to
        build, so the total always reads the same no matter how many runs it took.
      - spent  = what THIS run actually paid — i.e. only the pieces regenerated now. On a
        full fresh run spent == amount; on a repair/redo that reuses most pieces, spent is
        just the few that were remade. Defaults to amount when a step doesn't reuse anything."""
    if spent is None:
        spent = amount
    data.setdefault("costs", {})[key] = {"label": label,
                                         "amount": round(amount, 4),
                                         "spent": round(spent, 4)}


def reset_spent(data: dict):
    """Zero the 'spent this run' figure on every recorded cost. Called at the start of a
    REDO/REPAIR run so the 'paid this run' line counts only the pieces this run regenerates:
    the reused pieces (and the steps that don't run at all) contribute $0 to what was spent,
    while their real price still stands in the video total."""
    for entry in data.get("costs", {}).values():
        entry["spent"] = 0.0


def print_summary(data: dict):
    """Print a per-video cost breakdown (what each amount paid for) + the total."""
    costs_map = data.get("costs", {})
    footer = "(scraping + final assembly are free; ElevenLabs & OpenAI are estimates)"

    # Collect the rows in pipeline order (known steps first, then any extras). Each row
    # carries the TRUE price (amount) and what THIS run paid for it (spent).
    rows = []
    seen = set()
    for key in _SUMMARY_ORDER + [k for k in costs_map if k not in _SUMMARY_ORDER]:
        entry = costs_map.get(key)
        if not entry or key in seen:
            continue
        seen.add(key)
        rows.append((entry["label"], entry["amount"], entry.get("spent", entry["amount"])))

    if not rows:
        print("\n" + "=" * 72)
        print("  COST OF THIS VIDEO")
        print("=" * 72)
        print("  (no costs were recorded)")
        print("=" * 72)
        return

    total = sum(a for _, a, _ in rows)          # the finished video's real price
    spent = sum(s for _, _, s in rows)          # what THIS run actually paid
    # Pad every label to the longest one so ALL prices start in the same column.
    label_w = max([len(l) for l, _, _ in rows] + [len("TOTAL"), len("YOU PAID THIS RUN")])
    box = max(label_w + 14, len(footer) + 2)   # box wide enough for labels + footer

    print("\n" + "=" * box)
    print("  COST OF THIS VIDEO")
    print("=" * box)
    for label, amount, _ in rows:
        print(f"  {label:<{label_w}}   ${amount:>8.4f}")
    print("  " + "-" * (box - 2))
    print(f"  {'TOTAL':<{label_w}}   ${total:>8.4f}")
    # On a repair/redo most pieces are reused (spent $0) but still counted in the total above,
    # so the total is the whole video's price. Add a line showing what this run actually cost,
    # but only when it differs — on a full fresh run everything was paid, so spent == total.
    if total - spent > 0.0001:
        print(f"  {'YOU PAID THIS RUN':<{label_w}}   ${spent:>8.4f}")
        print(f"  (the rest was reused from the last run at no cost)")
    print(f"  {footer}")
    print("=" * box)


def openai_cost(input_tokens: int, output_tokens: int) -> float:
    """Work out the real dollar cost of one OpenAI call from its token usage."""
    return (
        input_tokens / 1_000_000 * OPENAI_INPUT_PER_1M
        + output_tokens / 1_000_000 * OPENAI_OUTPUT_PER_1M
    )
