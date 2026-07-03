"""REAL cost from the spy log — no estimation.

Reads output/api_calls.jsonl (written by costlog.py during a COSTLOG=1 run) and
turns the REAL billed quantities into dollars.

HOW fal billing actually works (verified on the live run + fal's official docs):
  - fal returns the billed amount in the response header  x-fal-billable-units.
  - That number is the count of the model's OWN native unit, and each model has
    its own price per unit:
        nano-banana-pro / .../edit            1 unit  = 1 image        $0.15
        flux/dev                              1 unit  = 1 image        $0.025
        kling-video/v3 standard i2v           1 unit  = 1 SECOND       $0.154   (voice tier)
        bytedance/seedance/v1.5/pro i2v       1 unit  = 1 MILLION tok  $1.20    (720p, no audio)
        kling-video/create-voice              1 unit  = 1 generation   $0.007
    So cost = billable_units x price_per_unit.  (Seedance's header is already in
    millions of tokens, e.g. 0.1737 -> 0.1737 x $1.20 = $0.208.)

  - IMPORTANT quirk: the header rides on the RESULT-fetch GET, whose URL fal
    TRUNCATES to the namespace root (…/kling-video/requests/<id>, …/flux/…,
    …/bytedance/…). The full model name only appears on the earlier submit POST.
    So we recover the real model by matching each billed result back to the
    submit POSTs of the same namespace, in order (FIFO). The pipeline submits
    sequentially per namespace, so order is exact.

Other services:
  - OpenAI:     real token cost, taken from analysis.json (the scripts compute it
                from the actual token usage the API returned — already exact).
  - ElevenLabs: real character count from the request bodies x the plan rate.

Anything billed that we can't map to a verified price is listed as UNVERIFIED
instead of being guessed — so nothing is ever estimated silently.

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
PRICES = {
    "nano-banana-pro/edit": 0.15,   # $0.15 / composed image (2K)   (check /edit first)
    "nano-banana-pro": 0.15,        # $0.15 / portrait image (2K)
    "flux/dev": 0.025,              # $0.025 / image (1 MP)
    "kling-video/v3/standard/image-to-video": 0.154,      # $0.154 / SECOND (voice tier)
    "bytedance/seedance/v1.5/pro/image-to-video": 1.20,   # $1.20 / MILLION tokens (720p, no audio)
    "kling-video/create-voice": 0.007,                    # $0.007 / generation
}
ELEVEN_PER_1K_CHARS = 0.30          # plan-dependent; the char count itself is real


def full_model(url_model: str):
    """Return the full model key from a SUBMIT url's model field, or None."""
    return next((k for k in PRICES if url_model.startswith(k)), None)


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
                        submits.setdefault(base_ns(fm), []).append(fm)
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
    rows = {}         # full_model -> [total_cost, total_units, calls]
    unverified = {}   # base_ns -> units we couldn't map
    for ns, units in results:
        queue = submits.get(ns)
        fm = queue.pop(0) if queue else None
        if not fm:
            unverified[ns] = unverified.get(ns, 0) + units
            continue
        cost = units * PRICES[fm]
        r = rows.setdefault(fm, [0.0, 0.0, 0])
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
    for fm, (cost, units, calls) in sorted(rows.items()):
        total += cost
        print(f"  fal {fm:<44} {units:>8.3f} u x ${PRICES[fm]:<6} = ${cost:>8.4f}  ({calls} calls)")
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
