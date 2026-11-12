"""Single source of truth for model prices and the model-id matcher. Stdlib only.

USD per million tokens. Source: https://platform.claude.com/docs/en/about-claude/pricing (fetched 2026-11-12).
Claude Code writes the 1-hour prompt cache, so "write" is the 1h cache-write price (2x base input), NOT the 5m one
(1.25x input); the 5m price is kept as "write5m" for reference. These are API-equivalent units: on a Claude
subscription (Pro/Max) they are relative weights of consumption, not an invoice.

Verified rows (all fields read from the official table): opus 5.5, sonnet 5.5, haiku 5.5 (+ its >100k tier),
haiku 4.5, fable 5.2. Nothing here is UNVERIFIED. Older model generations (Opus 5/4.x, Sonnet 5/4.x, Fable 5/5.1)
are NOT priced separately: they match their family and are billed at the current family rate (approximation).
Users can override any field from ~/.tacano.json under "prices" (see jevlib.config)."""
import copy

PRICES = {
    "opus":    {"in": 4.0,  "out": 20.0, "read": 0.20, "write": 8.0,  "write5m": 5.0},
    "sonnet":  {"in": 2.0,  "out": 10.0, "read": 0.10, "write": 4.0,  "write5m": 2.5},
    "haiku":   {"in": 0.10, "out": 0.50, "read": 0.01, "write": 0.20, "write5m": 0.125,
                "long_from": 100_000,   # prompts over this many tokens (incl. cache) bill at "long"
                "long": {"in": 0.50, "out": 2.50, "read": 0.05, "write": 1.00, "write5m": 0.625}},
    "haiku-4": {"in": 1.0,  "out": 5.0,  "read": 0.10, "write": 2.0,  "write5m": 1.25},   # Haiku 4.5
    "fable":   {"in": 8.0,  "out": 40.0, "read": 0.20, "write": 16.0, "write5m": 10.0},   # Fable 5.2 (also Mythos 5.2)
}
FALLBACK = "opus"          # unknown model, '<synthetic>', None: behave as before (Opus)
# Order matters: 'haiku-4' before 'haiku'.
_MATCH = (("haiku-4", "haiku-4"), ("haiku", "haiku"), ("fable", "fable"), ("mythos", "fable"),
          ("opus", "opus"), ("sonnet", "sonnet"))


def family(model):
    """Price-table key for a model id (case-insensitive substring), or None if unknown/synthetic."""
    m = (model or "").lower()
    if not m or m.startswith("<"):
        return None
    for needle, key in _MATCH:
        if needle in m:
            return key
    return None


def base_family(model):
    """Like family() but falls back to Opus (current behaviour) for unknown models."""
    return family(model) or FALLBACK


def default_prices():
    return copy.deepcopy(PRICES)


def merge_prices(over, base=None):
    """Deep-merge a user override ({family: {field: value, 'long': {...}}}) onto the defaults.
    Accepts the old config format (partial dicts without 'in'). Ignores malformed entries."""
    out = copy.deepcopy(base or PRICES)
    if not isinstance(over, dict):
        return out
    for fam, v in over.items():
        if not isinstance(v, dict):
            continue
        dst = out.setdefault(fam, {})
        for k, x in v.items():
            if k == "long" and isinstance(x, dict):
                dst.setdefault("long", {}).update(x)
            else:
                dst[k] = x
    return out


def rates(p, prompt_tokens):
    """Per-MTok rates for a call whose prompt is prompt_tokens long (handles tiered models)."""
    return p["long"] if "long_from" in p and prompt_tokens > p["long_from"] else p


def price(model, prompt_tokens=0, table=None):
    """(in, out, read, write) for a model id; unknown -> Opus."""
    r = rates((table or PRICES)[base_family(model)], prompt_tokens)
    return r["in"], r["out"], r["read"], r["write"]
