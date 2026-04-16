"""
LLM-based classifier: fills counterparty, counterparty_category, and any
txn_type / upi_type that the rules classifier left as None.

Supports three providers (Anthropic, OpenAI, Google) via a thin abstraction
layer.  Includes:
  - Batching (group N narrations per API call)
  - Caching (identical narrations never re-call the LLM)
  - Token tracking (LLMResponse carries input/output token counts for costing)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pipeline import config as _cfg
from pipeline.config import (
    ANTHROPIC_API_KEY,
    GOOGLE_API_KEY,
    LLM_BATCH_SIZE,
    LLM_CACHE_DIR,
    LLM_FALLBACK_CHAIN,
    LLM_MODEL_MAP,
    OPENAI_API_KEY,
)
from pipeline.models import (
    CanonicalTransaction,
    Channel,
    ClassificationSource,
    CounterpartyCategory,
    Direction,
    SpendCategory,
    TxnType,
    UPIType,
)
from pipeline.prompts import batch_classify_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLMResponse — structured return from every provider call
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_llm(txns: list[CanonicalTransaction]) -> list[CanonicalTransaction]:
    """Run LLM classification on transactions that still have gaps.

    Supports three modes via ``LLM_MODEL``:
      - ``"none"``  — skip LLM entirely (rules-only)
      - ``"auto"``  — use the fallback chain (try models in order)
      - a specific model key — use exactly that model, no fallback
    """
    # Read at call time so CLI overrides (config.LLM_MODEL = "none") are respected
    llm_model = _cfg.LLM_MODEL

    if llm_model == "none":
        return txns

    work = _build_work_items(txns)
    if not work:
        logger.debug("LLM: all fields already filled — nothing to do")
        return txns

    num_batches = (len(work) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
    logger.info("LLM: %d transactions need classification, batching into %d calls",
                len(work), num_batches)

    if llm_model == "auto":
        model_chain = LLM_FALLBACK_CHAIN
        logger.info("LLM: auto mode — fallback chain: %s", " → ".join(model_chain))
    else:
        if llm_model not in LLM_MODEL_MAP:
            raise ValueError(
                f"Unknown LLM_MODEL={llm_model!r}. "
                f"Valid: {list(LLM_MODEL_MAP)}, 'auto', or 'none'."
            )
        model_chain = [llm_model]

    # Process each batch, using the fallback chain per batch if needed
    for batch_start in range(0, len(work), LLM_BATCH_SIZE):
        batch = work[batch_start : batch_start + LLM_BATCH_SIZE]
        items = [item for _, item in batch]
        indices = [idx for idx, _ in batch]

        # Check cache first (keyed by content, not model — any model's
        # cached result for the same batch is reusable)
        cache_key = _batch_cache_key(items)
        cache = _load_cache_for_key(cache_key)
        if cache is not None:
            _apply_results(txns, indices, items, cache)
            continue

        results = _call_with_fallback(model_chain, items)
        if results is not None:
            _save_cache_for_key(cache_key, results)
            _apply_results(txns, indices, items, results)
        else:
            logger.warning("LLM: all models failed for batch starting at index %d", batch_start)

    return txns


def _call_with_fallback(
    model_chain: list[str],
    items: list[dict],
) -> list[dict] | None:
    """Try each model in the chain until one returns valid results."""
    system_msg, user_msg = batch_classify_prompt(items)

    for model_key in model_chain:
        provider, model_id = LLM_MODEL_MAP[model_key]
        try:
            response = _call_llm(provider, model_id, system_msg, user_msg)
            results = _parse_response(response.text)
            if results:
                logger.info("LLM: ✓ %s (%d+%d tokens)",
                            model_key, response.input_tokens, response.output_tokens)
                return results
            logger.warning("LLM: %s returned empty/unparseable response, trying next...", model_key)
        except Exception as e:
            logger.warning("LLM: %s failed: %s, trying next...", model_key, e)

    return None


# ---------------------------------------------------------------------------
# Work-item builder — sends ALL available fields to the LLM
# ---------------------------------------------------------------------------

def _build_work_items(txns: list[CanonicalTransaction]) -> list[tuple[int, dict]]:
    """Build the list of (index, context_dict) for transactions needing LLM help."""
    work: list[tuple[int, dict]] = []
    for idx, txn in enumerate(txns):
        needs = _fields_needed(txn)
        if not needs:
            continue
        work.append((idx, build_txn_context(txn, needs)))
    return work


def build_txn_context(txn: CanonicalTransaction, needs: list[str]) -> dict:
    """Package all transaction fields into a dict for the LLM prompt.

    Exposed as a public helper so the benchmark script can reuse it.
    """
    return {
        "id": txn.txn_id,
        "txn_date": str(txn.txn_date),
        "desc": txn.raw_description,
        "direction": txn.direction.value,
        "amount": str(txn.amount),
        "channel": txn.channel.value if txn.channel else "",
        "txn_type": txn.txn_type.value if txn.txn_type else "",
        "upi_type": txn.upi_type.value if txn.upi_type else "",
        "ref_number": txn.ref_number or "",
        "needs": ", ".join(f'"{n}"' for n in needs),
    }


def _fields_needed(txn: CanonicalTransaction) -> list[str]:
    """Return list of field names that still need to be filled."""
    needs = []
    if txn.txn_type is None:
        needs.append("txn_type")
    if txn.channel is not None and txn.channel.value in ("UPI",) and txn.upi_type is None:
        needs.append("upi_type")
    if txn.counterparty is None:
        needs.append("counterparty")
    if txn.counterparty_category is None:
        needs.append("counterparty_category")
    # Only ask LLM to fill spend_category for OUTFLOW transactions where it's still None.
    # INFLOW transactions don't need a spend_category (the rules classifier already skips them).
    if txn.spend_category is None and txn.direction == Direction.OUTFLOW:
        needs.append("spend_category")
    return needs


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _call_llm(provider: str, model_id: str, system: str, user: str) -> LLMResponse:
    """Dispatch to the appropriate provider SDK and return structured response."""
    if provider == "openai":
        return _call_openai(model_id, system, user)
    elif provider == "anthropic":
        return _call_anthropic(model_id, system, user)
    elif provider == "google":
        return _call_google(model_id, system, user)
    raise ValueError(f"Unknown provider: {provider}")


def _call_openai(model: str, system: str, user: str) -> LLMResponse:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # GPT-5 models don't support temperature; older models do.
    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if not model.startswith("gpt-5"):
        kwargs["temperature"] = 0.0

    resp = client.chat.completions.create(**kwargs)

    return LLMResponse(
        text=resp.choices[0].message.content or "",
        input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
        output_tokens=resp.usage.completion_tokens if resp.usage else 0,
    )


def _call_anthropic(model: str, system: str, user: str) -> LLMResponse:
    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.0,
    )
    # Anthropic's content list is a union of many block types; we only expect
    # TextBlock here. Guard defensively — if we ever get a different type,
    # return empty text so the fallback chain can try the next model.
    first_block = resp.content[0]
    text = first_block.text if hasattr(first_block, "text") else ""  # type: ignore[union-attr]
    return LLMResponse(
        text=text,
        input_tokens=resp.usage.input_tokens or 0,
        output_tokens=resp.usage.output_tokens or 0,
    )


def _call_google(model: str, system: str, user: str) -> LLMResponse:
    from google import genai

    client = genai.Client(api_key=GOOGLE_API_KEY)
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=genai.types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
        ),
    )

    # Some preview models return empty candidates (safety filter / model issue).
    # Gracefully handle by returning empty text instead of raising.
    try:
        text = resp.text or ""
    except (ValueError, AttributeError):
        text = ""

    meta = resp.usage_metadata
    return LLMResponse(
        text=text,
        input_tokens=(meta.prompt_token_count or 0) if meta else 0,
        output_tokens=(meta.candidates_token_count or 0) if meta else 0,
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> list[dict]:
    """Extract the JSON array from the LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("LLM: could not parse response: %s", text[:200])
        return []


def _apply_results(
    txns: list[CanonicalTransaction],
    indices: list[int],
    items: list[dict],
    results: list[dict],
) -> None:
    """Map LLM results back onto CanonicalTransaction objects."""
    result_map: dict[str, dict] = {}
    for r in results:
        if "id" in r:
            result_map[r["id"]] = r

    for idx, item in zip(indices, items):
        txn = txns[idx]
        r = result_map.get(item["id"], {})
        llm_touched = False

        if "txn_type" in r and txn.txn_type is None:
            try:
                txn.txn_type = TxnType(r["txn_type"])
                llm_touched = True
            except ValueError:
                pass

        if "upi_type" in r and txn.upi_type is None:
            try:
                txn.upi_type = UPIType(r["upi_type"])
                llm_touched = True
            except ValueError:
                pass

        if "counterparty" in r and txn.counterparty is None:
            txn.counterparty = str(r["counterparty"]).strip()
            llm_touched = True

        if "counterparty_category" in r and txn.counterparty_category is None:
            try:
                txn.counterparty_category = CounterpartyCategory(r["counterparty_category"])
                llm_touched = True
            except ValueError:
                cat_val = str(r["counterparty_category"]).strip()
                for member in CounterpartyCategory:
                    if member.value.lower() == cat_val.lower():
                        txn.counterparty_category = member
                        llm_touched = True
                        break

        if "spend_category" in r and txn.spend_category is None:
            try:
                txn.spend_category = SpendCategory(r["spend_category"])
                llm_touched = True
            except ValueError:
                pass  # LLM returned an invalid value — leave None, don't crash

        if llm_touched:
            txn.classification_source = ClassificationSource.LLM

        _enforce_consistency(txn)


def _enforce_consistency(txn: CanonicalTransaction) -> None:
    """Fix logically impossible field combinations after LLM fills values.

    The LLM occasionally returns a txn_type that contradicts the channel
    (e.g. BANK_TRANSFER for a UPI transaction).  This guard corrects those
    after the fact so we don't pollute the database.
    """
    if txn.channel == Channel.UPI and txn.txn_type == TxnType.BANK_TRANSFER:
        txn.txn_type = TxnType.UPI_TRANSFER

    if txn.channel == Channel.UPI and txn.upi_type == UPIType.P2P:
        if txn.txn_type not in (TxnType.UPI_TRANSFER, TxnType.SELF_TRANSFER):
            txn.txn_type = TxnType.UPI_TRANSFER


# ---------------------------------------------------------------------------
# Cache helpers
#
# Cache is now model-independent: keyed by batch content hash.  Since we use
# multi-model fallback, the *same* batch might be served by different models
# on different runs.  The cache stores whichever model's result we got first;
# subsequent runs reuse it regardless of which model is currently primary.
# ---------------------------------------------------------------------------

_CACHE_FILE = "classify_cache.json"


def _batch_cache_key(items: list[dict]) -> str:
    blob = json.dumps(
        [(i["id"], i["desc"]) for i in items], sort_keys=True
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_path() -> Path:
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return LLM_CACHE_DIR / _CACHE_FILE


def _load_full_cache() -> dict:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _load_cache_for_key(key: str) -> list[dict] | None:
    """Return cached results for a batch key, or None if not cached."""
    cache = _load_full_cache()
    return cache.get(key)


def _save_cache_for_key(key: str, results: list[dict]) -> None:
    """Persist a single batch result to the cache file."""
    cache = _load_full_cache()
    cache[key] = results
    path = _cache_path()
    path.write_text(json.dumps(cache, indent=2))
