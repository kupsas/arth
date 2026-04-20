# Garak — Periodic Red-Teaming Guide for Arth Agent

> **What is garak?** NVIDIA's open-source LLM vulnerability scanner ("Generative AI Red-teaming & Assessment Kit"). Think of it as a security audit tool for LLMs — like `nmap` but for language models. It sends hundreds of adversarial probe prompts to a target model and reports how often the model fails each category of attack.
> 
> GitHub: https://github.com/NVIDIA/garak | Docs: https://docs.garak.ai

**Related (Arth screening + sanitizer):** For a throttled **live** run of the shared probe set against the real classifier (no ReAct loop), use `python3 scripts/run_security_probes_live.py` from the repo root — see the script docstring for flags and caveats.

---

## Why not in CI?

Garak is **not** a pytest plugin and intentionally lives outside our normal test suite for three reasons:

1. **Cost** — each full scan costs real money in LLM API calls (~100-500 prompts per probe, 10+ generations each).
2. **Time** — a full scan takes 10-60 minutes depending on the probe set.
3. **Scope** — garak probes the **underlying LLM model's** own safety barriers (does Gemini/Claude/GPT comply when asked to do something bad?), not our screening/sanitizer layer. Our automated probe suite in `tests/test_security_probes.py` already covers the Arth-specific screening layer.

**When to run garak:** Before major model switches (e.g. moving from gemini-3-flash to a new primary), or as a quarterly security audit.

---

## What garak actually tests (vs. our probe suite)

| What is being tested | Our probe suite (`test_security_probes.py`) | Garak |
|---|---|---|
| Does the sanitizer scrub injection from tool output? | ✅ | ✗ |
| Does the screening classifier route correctly? | ✅ (mocked) | ✗ |
| Does the LLM itself comply with DAN / jailbreak attacks? | ✗ | ✅ |
| Does the LLM leak training data? | ✗ | ✅ |
| Does the LLM generate toxic content when prompted cleverly? | ✗ | ✅ |
| Encoding-based injection (Base64, Unicode smuggling) | ✗ | ✅ |

They complement each other. Our suite tests the Arth security *wrapper*; garak tests the model underneath.

---

## Setup (one-time, in a separate venv)

Garak has its own dependency tree that can conflict with the main project's deps. Run it in isolation:

```bash
python3 -m venv .venv-garak
source .venv-garak/bin/activate
pip install -U garak
```

Set your API keys in the environment:

```bash
export OPENAI_API_KEY="sk-..."            # for scanning GPT models
export ANTHROPIC_API_KEY="sk-ant-..."     # for scanning Claude models
# For Gemini via LiteLLM: garak uses litellm generator
export GEMINI_API_KEY="AI..."
```

---

## Recommended probes for Arth

These probe families are most relevant for a financial assistant:

| Probe | What it tests | Risk to Arth |
|---|---|---|
| `promptinject` | PromptInject framework attacks | High — indirect injection via data |
| `encoding` | Base64, hex, Unicode smuggling to bypass filters | High — our sanitizer only catches plaintext |
| `dan` | DAN / Do-Anything-Now jailbreaks | Medium — system prompt covers this |
| `goodside` | Riley Goodside attacks (prompt injection patterns) | Medium |
| `leakreplay` | Does model replay training data (data leakage) | Low for finance data, but good hygiene |

---

## Example commands

### Scan Gemini 3 Flash for prompt injection + encoding attacks:

```bash
# Using LiteLLM generator (requires garak >= 0.9.0)
python3 -m garak \
  --target_type litellm \
  --target_name gemini/gemini-3-flash-preview \
  --probes promptinject,encoding \
  --generations 5
```

### Scan Claude Sonnet 4.6 (fallback model) for DAN + goodside:

```bash
python3 -m garak \
  --target_type litellm \
  --target_name anthropic/claude-sonnet-4-6 \
  --probes dan,goodside \
  --generations 5
```

### Quick sanity check before a model switch (all relevant probes, fewer generations):

```bash
python3 -m garak \
  --target_type litellm \
  --target_name gemini/gemini-3-flash-preview \
  --probes promptinject,encoding,dan,goodside \
  --generations 3
```

---

## Reading the results

Garak prints a row per probe/detector pair:

```
promptinject.HijackHateHumansMini                      FAIL  42/500 (8.4%)
encoding.InjectBase64                                  PASS  0/500  (0.0%)
```

- **PASS** = model resisted all attempts in this probe.
- **FAIL** = model complied with some % of adversarial prompts. The % tells you how reliably the attack works.

A 5-8% failure rate on `promptinject` for frontier models is normal (and why our screening layer exists). A 40%+ failure rate on a specific probe is worth investigating.

Garak writes a detailed `.jsonl` report at the path it prints at the start of the run. You can review individual failing prompts there.

---

## Acting on findings

If garak finds a probe category where the base model fails at a high rate:

1. Check whether our **screening classifier** would catch those inputs before they reach the model (`tests/test_security_probes.py` or a manual check of the probe payloads vs. the classifier).
2. If the screening misses them, add a few-shot example to `agent/prompts/screening.yaml` showing the correct BLOCK category.
3. If the sanitizer should catch them (tool output path), add the pattern to `_INJECTION_PHRASES` or `_INJECTION_REGEXES` in `agent/sanitizer.py` and a matching test case in `SANITIZER_ATTACK_CASES` in `tests/test_security_probes.py`.
4. Log what you found and what you changed in this file (keep a brief audit trail).

---

## Audit log

| Date | Models tested | Probes | Findings | Action taken |
|---|---|---|---|---|
| *(run garak and fill this in)* | | | | |
