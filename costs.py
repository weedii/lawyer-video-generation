"""Model prices — VERIFIED from official sources (checked June 2026), not guessed.
Every script prints its cost using these so we always know what we spent.

Sources (fal.ai official model pages + docs, checked 2026-06):
- Nano Banana Pro:  $0.15 / image (2K standard; 4K = double)
    fal.ai/models/fal-ai/nano-banana-pro
- FLUX.1 [dev]:     $0.025 / megapixel (rounded up to nearest MP)
    fal.ai/models/fal-ai/flux/dev
- FLUX.1 [schnell]: $0.003 / megapixel (rounded up)
    fal.ai/models/fal-ai/flux/schnell
- Sana:             $0.001 / megapixel
    fal.ai/models/fal-ai/sana
- fast-sdxl:        $0.00111 / compute-second (so price varies with run time)
    gist.github.com/azer/6e8ffa228cb5d6f5807cd4d895b191a4
- VEED Fabric 1.0 (DEFAULT talker): $0.08/sec (480p), $0.15/sec (720p). No padding.
    fal.ai/models/veed/fabric-1.0
- Kling AI Avatar v2 standard: $0.0562 / second of OUTPUT video (pads to ~7.2s)
    fal.ai/models/fal-ai/kling-video/ai-avatar/v2/standard
- OmniHuman 1.5:    $0.16 / second of output video
    fal.ai/models/fal-ai/bytedance/omnihuman/v1.5
- Seedance 1.5 Pro (SCENE model): $0.052/sec with audio, $0.026/sec without
    fal.ai/models/fal-ai/bytedance/seedance/v1.5/pro/image-to-video
- ElevenLabs and OpenAI bill on SEPARATE accounts (NOT fal).
"""

# Our generated images are 720x1280 = 0.88 MP, which fal ROUNDS UP to 1 MP.
# So at our size, the "per megapixel" prices below equal the "per image" cost.
OUR_IMAGE_MEGAPIXELS = 1

# --- Image models (fal) ---------------------------------------------------
# Nano Banana Pro (character portraits): flat per image. We generate 2K, which
# is the standard rate ($0.15). 4K would be double; we don't use 4K.
NANO_BANANA_PRO_PER_IMAGE = 0.15

# Nano Banana Pro EDIT (compose 2 characters into one scene shot): same flat
# per-image price as generate at 2K. Used by scene_clips.py.
#     fal.ai/models/fal-ai/nano-banana-pro/edit
NANO_BANANA_PRO_EDIT_PER_IMAGE = 0.15

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

# OpenAI GPT-4.1 published rate, per 1,000,000 tokens. Still tiny (~$0.08/video)
# next to the Kling clips. (gpt-4o-mini was 0.15/0.60 but too weak — see docs.)
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
_SUMMARY_ORDER = ["analyze", "images", "script", "voices", "clips"]


def record(data: dict, key: str, label: str, amount: float):
    """Save one step's real cost into the analysis data, so the whole-video
    total can be printed when the pipeline finishes."""
    data.setdefault("costs", {})[key] = {"label": label, "amount": round(amount, 4)}


def print_summary(data: dict):
    """Print a per-video cost breakdown (what each amount paid for) + the total."""
    costs_map = data.get("costs", {})
    footer = "(scraping + final assembly are free; ElevenLabs & OpenAI are estimates)"

    # Collect the rows in pipeline order (known steps first, then any extras).
    rows = []
    seen = set()
    for key in _SUMMARY_ORDER + [k for k in costs_map if k not in _SUMMARY_ORDER]:
        entry = costs_map.get(key)
        if not entry or key in seen:
            continue
        seen.add(key)
        rows.append((entry["label"], entry["amount"]))

    if not rows:
        print("\n" + "=" * 72)
        print("  COST OF THIS VIDEO")
        print("=" * 72)
        print("  (no costs were recorded)")
        print("=" * 72)
        return

    total = sum(a for _, a in rows)
    # Pad every label to the longest one so ALL prices start in the same column.
    label_w = max([len(l) for l, _ in rows] + [len("TOTAL")])
    box = max(label_w + 14, len(footer) + 2)   # box wide enough for labels + footer

    print("\n" + "=" * box)
    print("  COST OF THIS VIDEO")
    print("=" * box)
    for label, amount in rows:
        print(f"  {label:<{label_w}}   ${amount:>8.4f}")
    print("  " + "-" * (box - 2))
    print(f"  {'TOTAL':<{label_w}}   ${total:>8.4f}")
    print(f"  {footer}")
    print("=" * box)


def openai_cost(input_tokens: int, output_tokens: int) -> float:
    """Work out the real dollar cost of one OpenAI call from its token usage."""
    return (
        input_tokens / 1_000_000 * OPENAI_INPUT_PER_1M
        + output_tokens / 1_000_000 * OPENAI_OUTPUT_PER_1M
    )
