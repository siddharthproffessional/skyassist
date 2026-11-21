"""SkyAssist AI gateway — every LLM call in the system goes through here.

Resilience (Round 3):
* Provider chain, in order: Gemini → Groq → Ollama (if running) → deterministic rules. A failure moves to the next
  provider; the rules fallback always answers, so the agent never crashes on an AI outage.
* Short per-call timeouts (SKYASSIST_LLM_TIMEOUT, default 8 s) and at most one retry, only for transient errors
  (timeout, connection error, HTTP 429/5xx). 4xx errors are not retried.
* Circuit breaker: after 3 consecutive failures a provider is skipped for 60 s, so a dead provider costs nothing.
* Every attempt is logged to `llm_calls`: purpose, provider, model, latency, tokens in/out (from the provider's own
  usage fields), outcome and error type. API keys and prompt text are never logged.
* Simulation switches (clearly labelled SIMULATED in the log) let the OCC demonstrate an outage or a slow provider
  on demand: they make the gateway behave exactly as it would for a real timeout/outage.

The LLM never decides anything safety-relevant: callers validate every output in code.
"""
import json
import os
import re
import threading
import time

try:
    import requests
except ImportError:  # rules-only mode still works
    requests = None

from db import tx, utcnow

TIMEOUT = float(os.getenv("SKYASSIST_LLM_TIMEOUT", "12"))
MAX_RETRIES = int(os.getenv("SKYASSIST_LLM_RETRIES", "1"))
BREAK_AFTER, BREAK_FOR = 3, 60.0
MODELS = {"gemini": os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
          "groq": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
          "ollama": os.getenv("OLLAMA_MODEL", "qwen3")}
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

_lock = threading.Lock()
_breaker = {}                      # provider -> {"fails": n, "open_until": ts}
_sim = {"outage": set(), "slow": set()}   # labelled demo switches


class Transient(Exception):
    pass


class Permanent(Exception):
    pass


def _key(p):
    return os.getenv({"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}.get(p, ""), "")


def _ollama_up():
    if requests is None:
        return False
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=0.5).ok
    except Exception:
        return False


def configured_chain():
    """Providers that are configured, in fallback order. LLM_BACKEND=rules disables AI entirely (tests, offline)."""
    forced = os.getenv("LLM_BACKEND", "auto")
    if forced == "rules" or requests is None:
        return []
    if forced in MODELS:
        return [forced]
    chain = [p for p in ("gemini", "groq") if _key(p)]
    if os.getenv("SKYASSIST_USE_OLLAMA", "0") == "1" and _ollama_up():
        chain.insert(0, "ollama")
    return chain


# ------------------------------------------------------------------ simulation switches (OCC demo)
def set_simulation(provider, outage=None, slow=None):
    with _lock:
        if outage is not None:
            (_sim["outage"].add if outage else _sim["outage"].discard)(provider)
        if slow is not None:
            (_sim["slow"].add if slow else _sim["slow"].discard)(provider)


def simulation_state():
    return {"outage": sorted(_sim["outage"]), "slow": sorted(_sim["slow"])}


def reset_breakers():
    with _lock:
        _breaker.clear()


def breaker_state():
    now = time.time()
    return {p: {"consecutive_failures": b["fails"], "open": b["open_until"] > now,
                "reopens_in_s": max(0, round(b["open_until"] - now))} for p, b in _breaker.items()}


def _breaker_open(p):
    b = _breaker.get(p)
    return bool(b and b["open_until"] > time.time())


def _record(p, ok):
    with _lock:
        b = _breaker.setdefault(p, {"fails": 0, "open_until": 0})
        if ok:
            b["fails"] = 0
        else:
            b["fails"] += 1
            if b["fails"] >= BREAK_AFTER:
                b["open_until"] = time.time() + BREAK_FOR


# ------------------------------------------------------------------ provider adapters
def _post(url, **kw):
    try:
        r = requests.post(url, timeout=TIMEOUT, **kw)
    except requests.Timeout:
        raise Transient("timeout")
    except requests.ConnectionError:
        raise Transient("connection error")
    if r.status_code == 429 or r.status_code >= 500:
        raise Transient(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise Permanent(f"HTTP {r.status_code}")
    return r.json()


def _call_provider(p, system, user, json_mode):
    if p in _sim["outage"]:
        raise Transient("SIMULATED outage")
    if p in _sim["slow"]:
        time.sleep(min(TIMEOUT, 2))
        raise Transient("SIMULATED slow response (timeout)")
    if p == "gemini":
        cfg = {"temperature": 0, "maxOutputTokens": 1024}
        if "2.5-flash" in MODELS[p]:
            cfg["thinkingConfig"] = {"thinkingBudget": 0}   # extraction needs no reasoning tokens: cheaper, faster
        elif MODELS[p].startswith("gemini-3"):
            cfg["thinkingConfig"] = {"thinkingLevel": "low"}  # Gemini 3 thinks by default; keep it short
        if json_mode:
            cfg["responseMimeType"] = "application/json"
        d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODELS[p]}:generateContent",
                  headers={"x-goog-api-key": _key(p)},
                  json={"systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": cfg})
        try:
            text = d["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise Permanent("empty or blocked response")
        u = d.get("usageMetadata", {})
        return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    if p == "groq":
        body = {"model": MODELS[p], "temperature": 0, "max_tokens": 1500,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if "gpt-oss" in MODELS[p]:
            body["reasoning_effort"] = "low"     # reasoning tokens count against max_tokens; keep room for the JSON
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        d = _post("https://api.groq.com/openai/v1/chat/completions", json=body,
                  headers={"Authorization": f"Bearer {_key(p)}"})
        u = d.get("usage", {})
        return d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    if p == "ollama":
        body = {"model": MODELS[p], "stream": False, "think": False, "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["format"] = "json"
        d = _post(f"{OLLAMA_URL}/api/chat", json=body)
        return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)
    raise Permanent("unknown provider")


def _log(purpose, provider, attempt, ms, tin, tout, outcome, error, run_id):
    try:
        with tx() as con:
            con.execute("INSERT INTO llm_calls(created_at,purpose,provider,model,attempt,latency_ms,tokens_in,"
                        "tokens_out,outcome,error,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (utcnow(), purpose, provider, MODELS.get(provider, "rules"), attempt, round(ms, 1), tin, tout,
                         outcome, error, run_id))
    except Exception:
        pass  # logging must never break the request path (table missing on a v2-only DB)


def complete(system, user, json_mode=False, purpose="general", run_id=None):
    """Returns (text_or_None, label, trace). text None = every provider failed or none configured → the caller
    uses its deterministic fallback. `trace` lists each attempt for the UI."""
    trace = []
    chain = configured_chain()
    if not chain:
        return None, "rules", [{"provider": "rules", "outcome": "no AI provider configured"}]
    for p in chain:
        if _breaker_open(p):
            trace.append({"provider": p, "outcome": "skipped (circuit open)"})
            _log(purpose, p, 0, 0, 0, 0, "SKIPPED_BREAKER", "circuit open", run_id)
            continue
        for attempt in range(1, MAX_RETRIES + 2):
            t0 = time.perf_counter()
            try:
                text, tin, tout = _call_provider(p, system, user, json_mode)
                ms = (time.perf_counter() - t0) * 1000
                text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
                _record(p, True)
                _log(purpose, p, attempt, ms, tin, tout, "OK", None, run_id)
                trace.append({"provider": p, "attempt": attempt, "ms": round(ms), "tokens": tin + tout,
                              "outcome": "ok"})
                return text, p, trace
            except Transient as e:
                ms = (time.perf_counter() - t0) * 1000
                _record(p, False)
                retry = attempt <= MAX_RETRIES and not str(e).startswith("SIMULATED outage")
                _log(purpose, p, attempt, ms, 0, 0, "RETRY" if retry else "FAILED", str(e), run_id)
                trace.append({"provider": p, "attempt": attempt, "ms": round(ms),
                              "outcome": f"failed: {e}" + (" → retrying once" if retry else " → next provider")})
                if retry:
                    time.sleep(0.4 * attempt)
                    continue
                break
            except Exception as e:  # permanent: bad key, bad request, unexpected payload — do not retry
                ms = (time.perf_counter() - t0) * 1000
                _record(p, False)
                err = str(e) if isinstance(e, Permanent) else type(e).__name__
                _log(purpose, p, attempt, ms, 0, 0, "FAILED", err, run_id)
                trace.append({"provider": p, "attempt": attempt, "ms": round(ms), "outcome": f"failed: {err} → next provider"})
                break
    trace.append({"provider": "rules", "outcome": "fallback used"})
    _log(purpose, "rules", 1, 0, 0, 0, "FALLBACK", "all providers failed", run_id)
    return None, "rules (all AI providers failed)", trace


def complete_json(system, user, purpose="general", run_id=None):
    text, label, trace = complete(system, user, json_mode=True, purpose=purpose, run_id=run_id)
    if text is None:
        return None, label, trace
    try:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0) if m else text), label, trace
    except Exception:
        trace.append({"provider": label, "outcome": "invalid JSON → rules"})
        return None, f"{label} (invalid JSON → rules)", trace
