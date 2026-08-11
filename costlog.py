"""COST/REQUEST LOGGER — catches every external API call and what it billed.

Purpose: when running the workflow, log EVERY request + response to the paid
services (fal.ai, OpenAI, ElevenLabs) so we can see exactly what each operation
charged and verify it against the official prices.

How it works: it monkeypatches the HTTP layer both libraries sit on —
  - httpx.Client.send   (used by fal_client AND the OpenAI SDK)
  - requests Session     (used by the ElevenLabs calls)
so no pipeline script has to change. For each call it records the service, the
model (from the URL), the HTTP status, and the billing signal each service
returns:
  - fal:        response header  x-fal-billable-units  (the exact units billed)
  - ElevenLabs: any response header mentioning character/cost/credit
  - OpenAI:     token usage lives in the JSON body (the scripts read resp.usage);
                here we at least confirm the request/response happened.

Activation is OPT-IN via the COSTLOG env var (so normal runs are unaffected):
    COSTLOG=1 python run.py "<link>"
sitecustomize.py imports and enables this automatically for every subprocess
when COSTLOG is set.

Output:
  - a concise LIVE line to stderr for each billed call (streams in the terminal)
  - a full JSONL record per call appended to output/api_calls.jsonl
"""
import os
import sys
import json
import time
import threading

LOG_PATH = os.path.join("output", "api_calls.jsonl")
_PAID_HOSTS = ("fal.run", "fal.ai", "openai.com", "elevenlabs.io")

# The scene clips now render in PARALLEL, so several threads call _log at the same time. Guard
# the append so two threads can't interleave a half-written line into api_calls.jsonl (which
# would make reconcile.py choke on a corrupt record). The per-model billed-unit TOTALS reconcile
# computes are order-independent, so the only thing we must protect is the write itself.
_LOG_LOCK = threading.Lock()

# Header names (lowercased) that carry a billing signal on any service.
_COST_HINTS = ("billable", "cost", "credit", "character", "charge", "x-fal-billable-units")


def _service(host: str) -> str:
    if "fal" in host:
        return "fal"
    if "openai" in host:
        return "openai"
    if "elevenlabs" in host:
        return "elevenlabs"
    return "other"


def _model_from_url(url: str) -> str:
    """Pull a readable model/endpoint name out of the request URL path."""
    path = url.split("?")[0]
    for marker in ("/fal-ai/", "/veed/", "/bytedance/"):
        if marker in path:
            return path.split(marker, 1)[1]
    # openai / elevenlabs: last 2 path segments
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[-2:]) if parts else path


def _billing_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if any(h in lk for h in _COST_HINTS):
            out[k] = v
    return out


def _log(service: str, model: str, method: str, url: str, status,
         resp_headers: dict, req_body):
    host = url.split("/")[2] if "//" in url else url
    # Skip the noisy fal queue STATUS polls and file uploads (no billing there).
    low = url.lower()
    is_status_poll = low.endswith("/status")
    is_upload = "storage.googleapis" in low or "/files/" in low or "fal.media" in low
    billing = _billing_headers(resp_headers)

    record = {
        "t": round(time.time(), 3),
        "service": service,
        "model": model,
        "method": method,
        "url": url,
        "status": status,
        "billing_headers": billing,
    }
    # Keep a short request-body snippet (prompts/params), never the whole thing.
    if req_body is not None:
        try:
            s = req_body if isinstance(req_body, str) else json.dumps(req_body)
            record["request"] = s[:600]
        except Exception:
            pass

    # Full detail -> file (everything to the paid hosts). Locked so parallel clip renders can't
    # interleave two half-lines into the same record.
    try:
        os.makedirs("output", exist_ok=True)
        with _LOG_LOCK:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # Live line -> stderr, only for the calls that matter (a real billed event
    # or an OpenAI/ElevenLabs POST). Status polls / uploads stay out of the way.
    show = bool(billing) or (method == "POST" and not is_status_poll and not is_upload)
    if show:
        tag = f"[COST] {service}:{model} {method} -> {status}"
        if billing:
            tag += "  " + " ".join(f"{k}={v}" for k, v in billing.items())
        print(tag, file=sys.stderr, flush=True)


def enable():
    """Patch httpx + requests so every paid call is logged. Safe to call once."""
    if getattr(enable, "_done", False):
        return
    enable._done = True

    # --- httpx (fal_client + OpenAI SDK) ---
    try:
        import httpx

        _orig = httpx.Client.send

        def _send(self, request, **kwargs):
            resp = _orig(self, request, **kwargs)
            try:
                url = str(request.url)
                if any(h in url for h in _PAID_HOSTS):
                    body = None
                    try:
                        body = request.content.decode("utf-8", "ignore") if request.content else None
                    except Exception:
                        body = None
                    _log(_service(url.split("/")[2]), _model_from_url(url),
                         str(request.method), url, resp.status_code,
                         dict(resp.headers), body)
            except Exception:
                pass
            return resp

        httpx.Client.send = _send
    except Exception as e:
        print(f"[COST] httpx patch failed: {e}", file=sys.stderr)

    # --- requests (ElevenLabs) ---
    try:
        import requests

        _orig_req = requests.sessions.Session.request

        def _request(self, method, url, **kwargs):
            resp = _orig_req(self, method, url, **kwargs)
            try:
                if any(h in url for h in _PAID_HOSTS):
                    body = kwargs.get("json") or kwargs.get("data")
                    _log(_service(url.split("/")[2]), _model_from_url(url),
                         method, url, resp.status_code, dict(resp.headers), body)
            except Exception:
                pass
            return resp

        requests.sessions.Session.request = _request
    except Exception as e:
        print(f"[COST] requests patch failed: {e}", file=sys.stderr)

    print("[COST] logger enabled -> output/api_calls.jsonl", file=sys.stderr, flush=True)
