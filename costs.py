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
- Kling AI Avatar v2 standard: $0.0562 / second of OUTPUT video
    fal.ai/models/fal-ai/kling-video/ai-avatar/v2/standard
- OmniHuman 1.5:    $0.16 / second of output video
    fal.ai/models/fal-ai/bytedance/omnihuman/v1.5
- ElevenLabs and OpenAI bill on SEPARATE accounts (NOT fal).
"""

# Our generated images are 720x1280 = 0.88 MP, which fal ROUNDS UP to 1 MP.
# So at our size, the "per megapixel" prices below equal the "per image" cost.
OUR_IMAGE_MEGAPIXELS = 1

# --- Image models (fal) ---------------------------------------------------
# Nano Banana Pro (character portraits): flat per image. We generate 2K, which
# is the standard rate ($0.15). 4K would be double; we don't use 4K.
NANO_BANANA_PRO_PER_IMAGE = 0.15

# FLUX.1 [dev]: $0.025/MP -> $0.025 per image at our size. Used for the
# establishing shot and (currently) the anonymous silhouettes.
FLUX_DEV_PER_IMAGE = 0.025

# Cheaper options for the anonymous silhouette (it's just a dark shape, so image
# quality barely matters). Cost per image at our 1-MP size:
FLUX_SCHNELL_PER_IMAGE = 0.003    # $0.003 / MP
SANA_PER_IMAGE = 0.001            # $0.001 / MP
FAST_SDXL_PER_IMAGE = 0.0023      # ~$0.00111/compute-sec x ~2s; COMPUTE-billed, varies

# --- Talking-video models (fal) -------------------------------------------
# IMPORTANT (biggest cost in the whole pipeline): these bill on the OUTPUT video
# length, and Kling PADS every clip to a fixed ~7.2s block (longer than the
# speech). So a clip costs ~7.2s x $0.0562 = ~$0.40 EACH, no matter how short
# the line is. Narrator + anonymous lines are FREE (static image + voice via
# ffmpeg), so the way to spend less is FEWER Kling talking clips.
KLING_AVATAR_PER_SEC = 0.0562
KLING_PADDED_CLIP_SECONDS = 7.2                                   # fixed block Kling outputs
KLING_COST_PER_CLIP_EST = KLING_AVATAR_PER_SEC * KLING_PADDED_CLIP_SECONDS  # ~$0.40

# OmniHuman 1.5 (alternative, not wired): $0.16/sec but length MATCHES the audio
# (no padding). Break-even vs Kling is ~2.5s of audio: shorter lines are cheaper
# on OmniHuman, longer lines are cheaper on Kling. Most lines are >2.5s, so Kling
# usually wins — which is why we keep Kling.
OMNIHUMAN_PER_SEC = 0.16

# --- Text + voice (SEPARATE accounts, not fal) ----------------------------
# ElevenLabs bills by characters from your plan quota, not per-call dollars.
# This is a rough upper-bound estimate just so a number prints.
ELEVENLABS_PER_1K_CHARS = 0.30

# OpenAI gpt-4o-mini published rate, per 1,000,000 tokens. Tiny (~$0.002/video).
OPENAI_INPUT_PER_1M = 0.15    # $ per 1,000,000 input tokens
OPENAI_OUTPUT_PER_1M = 0.60   # $ per 1,000,000 output tokens


def show(label: str, amount: float):
    print(f"\n COST: {label} = ${amount:.4f}\n")


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
