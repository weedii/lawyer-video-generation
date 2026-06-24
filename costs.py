"""Cost estimates based on fal.ai public pricing (June 2026).
These are estimates computed from price lists, printed after each operation
so we always know what we spent.
"""

# FLUX.1 [dev]: $0.025 per megapixel, rounded up to nearest megapixel.
# Still used for the (no-people) establishing shot, where cheap is fine.
FLUX_DEV_PER_IMAGE = 0.025

# Nano Banana Pro (Google Gemini 3 Pro Image): top-tier photorealistic people,
# used for the character images. $0.15 per image at 1K/2K (4K would be double).
NANO_BANANA_PRO_PER_IMAGE = 0.15

# ElevenLabs: billed by characters from your plan quota (not per-call dollars).
# Rough upper-bound estimate so we see a number. Real cost depends on your plan.
ELEVENLABS_PER_1K_CHARS = 0.30

# Talking-video model (current): Kling AI Avatar v2 standard, per second of video.
# Tradeoff: pads short clips to a fixed ~7.2s block (assemble.py trims that off).
KLING_AVATAR_PER_SEC = 0.056

# OmniHuman 1.5 (alternative, not currently wired): $0.16/sec, 1080p, and its
# length matches the audio. ~3x Kling — kept here in case we switch back.
OMNIHUMAN_PER_SEC = 0.16

# OpenAI gpt-4o-mini: a very cheap text model. Priced per 1 million tokens.
# (Tokens = pieces of words. We compute the real cost from what the API reports.)
OPENAI_INPUT_PER_1M = 0.15    # $ per 1,000,000 input tokens
OPENAI_OUTPUT_PER_1M = 0.60   # $ per 1,000,000 output tokens


def show(label: str, amount: float):
    print(f"\n COST: {label} = ${amount:.4f}\n")


def openai_cost(input_tokens: int, output_tokens: int) -> float:
    """Work out the real dollar cost of one OpenAI call from its token usage."""
    return (
        input_tokens / 1_000_000 * OPENAI_INPUT_PER_1M
        + output_tokens / 1_000_000 * OPENAI_OUTPUT_PER_1M
    )
