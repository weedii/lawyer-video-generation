"""Cost estimates based on fal.ai public pricing (June 2026).
These are estimates computed from price lists, printed after each operation
so we always know what we spent.
"""

# FLUX.1 [dev]: $0.025 per megapixel, rounded up to nearest megapixel.
# We generate 720x1280 = 0.92 MP -> rounds up to 1 MP -> $0.025 per image.
FLUX_DEV_PER_IMAGE = 0.025

# Wan 2.2 5B image-to-video: $0.15 per clip. Does real vertical 9:16 (TikTok).
WAN_VIDEO_PER_CLIP = 0.15

# ElevenLabs: billed by characters from your plan quota (not per-call dollars).
# Rough upper-bound estimate so we see a number. Real cost depends on your plan.
ELEVENLABS_PER_1K_CHARS = 0.30

# SadTalker: cheapest talking model, billed by compute time (varies).
# Typical short clip lands around this. We confirm the real number after running.
SADTALKER_PER_CLIP_EST = 0.05

# Kling AI Avatar v2 (standard): realistic talking video, billed per second.
KLING_AVATAR_PER_SEC = 0.056

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
