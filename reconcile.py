"""REAL cost from the spy log — no estimation.

Reads output/api_calls.jsonl (written by costlog.py during a COSTLOG=1 run) and
turns the REAL billed quantities into dollars.

HOW fal billing actually works (verified on the live run + fal's official docs):
  - fal returns the billed amount in the response header  x-fal-billable-units.
  - That number is the count of the model's OWN native unit, and each model has
    its own price per unit:
        nano-banana-2 / .../edit              1 unit  = 1 image        $0.08 (1K)
        nano-banana-pro / .../edit            1 unit  = 1 image        $0.15
        flux/dev                              1 unit  = 1 image        $0.025
        bytedance/seedance/v1.5/pro i2v       1 unit  = 1 MILLION tok  $2.40 audio / $1.20 silent
        kling-video/create-voice              1 unit  = 1 generation   $0.007
    So cost = billable_units x price_per_unit.  (Seedance's header is already in
    millions of tokens, e.g. 0.1737 -> 0.1737 x $2.40 = $0.417 for a spoken clip.)
    Seedance's per-token price DOUBLES when native audio is on, so we read each
    submit's generate_audio flag and price spoken vs silent clips separately.

  - IMPORTANT quirk: the header rides on the RESULT-fetch GET, whose URL fal
    TRUNCATES to the namespace root (…/kling-video/requests/<id>, …/flux/…,
    …/bytedance/…). The full model name only appears on the earlier submit POST.
    So we recover the real model by matching each billed result back to the
    submit POSTs of the same namespace, in order (FIFO).

    Both the Nano Banana composes AND the Seedance clip renders now fire in PARALLEL
    (scene_clips.MAX_PARALLEL_RENDERS), so their submit/result log lines interleave and the FIFO
    order is no longer the submit order. That is STILL correct for the total, because within one
    namespace every call is billed at the SAME per-unit rate — every Seedance clip is SILENT, and
    every scene composite is nano-banana-2 at $0.08 (a Pro fallback lands in a DIFFERENT namespace,
    nano-banana-pro, priced on its own). So which submit pairs with which result never changes the
    priced sum, and the units are summed from the complete log either way. (If you ever run a MIX
    of audio and silent Seedance clips in parallel, the audio/silent split per clip could be
    mis-paired — re-serialise or tag the result with the request id if that ever matters.)

Other services:
  - OpenAI:     real token cost, taken from analysis.json (the scripts compute it
                from the actual token usage the API returned — already exact).
  - ElevenLabs: real character count from the request bodies x the plan rate.

Anything billed that we can't map to a verified price is listed as UNVERIFIED
instead of being guessed — so nothing is ever estimated silently.

NOTE: the CURRENT pipeline is memoir voiceover — every Seedance clip is SILENT and
there is NO lip-sync, so all Seedance jobs price at the no-audio rate and the old
re-dub models (fal-ai/sync-lipsync/v2, fal-ai/latentsync) no longer appear. If you
re-enable that legacy path and see them show up as UNVERIFIED, add their real
per-unit rate to PRICES from a live x-fal-billable-units header.

Usage:
    python reconcile.py
"""
import os
import sys
import json

LOG = os.path.join("output", "api_calls.jsonl")
ANALYSIS = os.path.join("output", "analysis.json")

# VERIFIED price per ONE of the model's native billable units (fal official
# pages, confirmed against the live run's headers).
# Seedance 1.5 pro (our scene model) bills per MILLION tokens, and the rate DOUBLES with
# native audio — so we price it per-call from the submit's generate_audio flag, not from
# the flat PRICES table below.
SEEDANCE_KEY = "bytedance/seedance/v1.5/pro/image-to-video"
SEEDANCE_AUDIO_PER_MTOK = 2.40     # $2.40 / MILLION tokens (720p WITH audio — spoken scenes)
SEEDANCE_NOAUDIO_PER_MTOK = 1.20   # $1.20 / MILLION tokens (720p no audio — silent inserts)

PRICES = {
    # ORDER MATTERS. "nano-banana" is a PREFIX of both "nano-banana-2" and
    # "nano-banana-pro", and full_model() takes the FIRST matching prefix — so the longer,
    # more specific keys must all come BEFORE the bare "nano-banana" ones at the bottom.
    #
    # Pro is the AUTOMATIC compose fallback: scene_image tries Nano Banana 2 first and
    # retries a hard image on Pro when NB2 returns nothing, so a normal run can legitimately
    # bill a few nano-banana-pro/edit images. Each tier prices correctly here because they
    # are separate namespaces (a failed NB2 attempt bills $0 and never reaches results).
    "nano-banana-pro/edit": 0.15,   # $0.15 / composed image        (check /edit first)
    "nano-banana-pro": 0.15,        # $0.15 / portrait image
    # Nano Banana 2 (CURRENT default) at the 1K tier we run. If you ever pass
    # resolution="2K" the real bill is 1.5x this ($0.12) and 4K is 2x ($0.16) — update
    # these numbers too, or the "REAL cost" printed here stops being real.
    "nano-banana-2/edit": 0.08,     # $0.08 / composed image (1K)
    "nano-banana-2": 0.08,          # $0.08 / portrait image (1K)
    # LEGACY non-pro — kept so older api_calls.jsonl logs still price correctly.
    # MUST stay LAST of the nano-banana keys (see the ORDER MATTERS note above).
    "nano-banana/edit": 0.039,      # $0.039 / composed image
    "nano-banana": 0.039,           # $0.039 / portrait image
    "flux/dev": 0.025,              # $0.025 / image (1 MP)
    SEEDANCE_KEY: SEEDANCE_AUDIO_PER_MTOK,   # default rate; overridden per-call by audio flag
    "kling-video/create-voice": 0.007,       # $0.007 / generation
}
ELEVEN_PER_1K_CHARS = 0.30          # plan-dependent; the char count itself is real


def full_model(url_model: str):
    """Return the full model key from a SUBMIT url's model field, or None."""
    return next((k for k in PRICES if url_model.startswith(k)), None)


def submit_has_audio(rec: dict) -> bool:
    """Read generate_audio out of a Seedance submit's (possibly truncated) request snippet.
    The clip renderer puts generate_audio at the FRONT of the body so it survives the
    600-char cut; we string-match rather than json.loads because the snippet is truncated.
    Defaults to True (Seedance's own default, and the dearer rate) when it can't be read."""
    body = (rec.get("request") or "").replace(" ", "").lower()
    if '"generate_audio":false' in body:
        return False
    return True


def base_ns(model_field: str) -> str:
    """Namespace ROOT fal uses on the truncated result url (…/<root>/requests/…).
    fal truncates the result url to the FIRST path segment, so a submit like
    'nano-banana-pro/edit' or 'kling-video/v3/standard/…' must key off the same
    root ('nano-banana-pro', 'kling-video') as its result GET does."""
    return model_field.split("/requests")[0].split("/")[0]


def billed_units(rec: dict):
    """The billable-units value fal put in the response header, or None."""
    for k, v in rec.get("billing_headers", {}).items():
        if "billable" in k.lower():
            try:
                return float(v)
            except ValueError:
                return None
    return None


def main():
    if not os.path.exists(LOG):
        sys.exit(f"No log at {LOG}. Run with:  COSTLOG=1 PYTHONPATH=. python run.py \"<link>\"")

    # Pass 1: read the log in order. Record, per namespace, the queue of full
    # model names that were SUBMITTED; and the list of billed RESULT events.
    submits = {}      # base namespace -> [full_model, full_model, ...] (in order)
    results = []      # [(base_ns, units), ...] in order
    eleven_chars = 0
    eleven_calls = 0

    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            svc = rec.get("service")
            model = rec.get("model", "")
            if svc == "fal":
                if rec.get("method") == "POST" and "/requests/" not in model:
                    fm = full_model(model)
                    if fm:
                        # Remember whether THIS Seedance submit had audio, so pass 2 can
                        # price it at the right (audio vs silent) per-token rate.
                        audio = submit_has_audio(rec) if fm == SEEDANCE_KEY else None
                        submits.setdefault(base_ns(fm), []).append((fm, audio))
                    continue
                units = billed_units(rec)
                if units is None:
                    continue          # status poll / upload — nothing billed
                results.append((base_ns(model), units))
            elif svc == "elevenlabs" and rec.get("method", "").upper() == "POST":
                body = rec.get("request", "")
                try:
                    eleven_chars += len(json.loads(body).get("text", ""))
                    eleven_calls += 1
                except Exception:
                    pass

    # Pass 2: match each billed result back to a submitted model of the same
    # namespace, in order (FIFO), and price it.
    rows = {}         # display label -> [total_cost, total_units, calls, unit_price]
    unverified = {}   # base_ns -> units we couldn't map
    for ns, units in results:
        queue = submits.get(ns)
        item = queue.pop(0) if queue else None
        if not item:
            unverified[ns] = unverified.get(ns, 0) + units
            continue
        fm, audio = item
        # Seedance is priced per-call by its audio flag; a spoken row and a silent row
        # are shown separately so each carries its correct per-token rate.
        if fm == SEEDANCE_KEY:
            price = SEEDANCE_AUDIO_PER_MTOK if audio else SEEDANCE_NOAUDIO_PER_MTOK
            label = f"{fm} ({'audio' if audio else 'silent'})"
        else:
            price = PRICES[fm]
            label = fm
        cost = units * price
        r = rows.setdefault(label, [0.0, 0.0, 0, price])
        r[0] += cost; r[1] += units; r[2] += 1

    # OpenAI: real cost the scripts already computed from real token usage.
    openai_cost = 0.0
    if os.path.exists(ANALYSIS):
        data = json.load(open(ANALYSIS))
        for key in ("analyze", "script"):
            entry = data.get("costs", {}).get(key)
            if entry:
                openai_cost += entry["amount"]

    # --- Print the real breakdown --------------------------------------------
    print("\n" + "=" * 74)
    print("  REAL COST OF THIS VIDEO  (from actual billed units — not estimated)")
    print("=" * 74)
    total = 0.0
    for label, (cost, units, calls, price) in sorted(rows.items()):
        total += cost
        print(f"  fal {label:<44} {units:>8.3f} u x ${price:<6} = ${cost:>8.4f}  ({calls} calls)")
    if eleven_calls:
        ecost = eleven_chars / 1000 * ELEVEN_PER_1K_CHARS
        total += ecost
        print(f"  elevenlabs TTS ({eleven_chars} real chars){'':<20}"
              f"~${ecost:>8.4f}  ({eleven_calls} calls, plan-rate)")
    if openai_cost:
        total += openai_cost
        print(f"  openai (real tokens, from analysis.json){'':<25}${openai_cost:>8.4f}")
    print("  " + "-" * 70)
    print(f"  {'REAL TOTAL':<56}${total:>8.4f}")
    if unverified:
        print("\n  ⚠ UNVERIFIED (billed but couldn't map to a price — NOT counted):")
        for ns, u in unverified.items():
            print(f"      {ns}: {u:.3f} billed units")
    print("=" * 74)


if __name__ == "__main__":
    main()
