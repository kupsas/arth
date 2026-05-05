"""
CLI entry point for the raw-to-canonical pipeline.

Usage:
    export ARTH_USER_ID=sashank   # same username as dashboard login (owns DB rows)
    python3 -m pipeline.run                                # default source, write to DB
    python3 -m pipeline.run --source hdfc_savings           # explicit source
    python3 -m pipeline.run --all-sources                   # run all 4 sources sequentially
    python3 -m pipeline.run --all-sources --llm none        # fast rules-only pass for all
    python3 -m pipeline.run --csv                           # legacy CSV output instead of DB
    python3 -m pipeline.run --validate                      # also run validator vs GSheet
    python3 -m pipeline.run --llm gemini-3.1-flash-lite     # force a specific model
    python3 -m pipeline.run --llm none                      # rules-only, no LLM

The pipeline stages run in order:
    1. Parse  →  2. Transform  →  3. Rules classify  →  4. LLM classify  →  5. Write (DB or CSV)
    (optional)  6. Validate against GSheet benchmark
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from pipeline import config
from pipeline.llm_classifier import classify_llm
from pipeline.logging_config import setup_logging
from pipeline.models import CanonicalTransaction
from pipeline.parsers import PARSER_REGISTRY
from pipeline.rules_classifier import classify_rules
from pipeline.transformer import transform
from pipeline.writer import write_csv

logger = logging.getLogger(__name__)


def _labelling_counts(canonical: list[CanonicalTransaction]) -> tuple[int, int]:
    """Return (labelled_count, may_need_review_count).

    A row counts as *labelled* when both transaction type and category are filled —
    anything else is worth a glance on the Review screen.
    """
    n = len(canonical)
    if n == 0:
        return 0, 0
    labelled = sum(
        1
        for t in canonical
        if t.txn_type is not None and t.counterparty_category is not None
    )
    return labelled, n - labelled


def _pipeline_cli_user_id() -> str:
    """Pipeline CLI must know which Arth user owns ``user_pipeline_sources`` rows."""
    uid = os.environ.get("ARTH_USER_ID", "").strip()
    if not uid:
        logger.error(
            "ARTH_USER_ID is not set. Export your Arth username, e.g. "
            "export ARTH_USER_ID=sashank (same string as dashboard login)."
        )
        sys.exit(1)
    return uid


def _run_single_source(
    source_key: str,
    *,
    input_file: str | None = None,
    write_to_csv: bool = False,
    output_file: str | None = None,
) -> list[CanonicalTransaction]:
    """Run the full pipeline for one source and persist results.

    Returns the list of enriched transactions (useful for validation).
    """
    if source_key not in PARSER_REGISTRY:
        logger.error("Unknown source: %r  Available: %s", source_key, list(PARSER_REGISTRY))
        sys.exit(1)

    user_id = _pipeline_cli_user_id()
    from api.database import get_engine, init_db
    from sqlmodel import Session

    init_db()
    with Session(get_engine()) as _cfg_session:
        source_cfgs = config.get_source_configs(user_id, _cfg_session)
    if source_key not in source_cfgs:
        logger.error(
            "No DB pipeline source for user_id=%r, source_key=%r. "
            "Configured keys for this user: %s",
            user_id,
            source_key,
            sorted(source_cfgs),
        )
        sys.exit(1)
    source_cfg = source_cfgs[source_key]
    parser_cls = PARSER_REGISTRY[source_key]
    parser = parser_cls()

    resolved_input = input_file or config.DATA_DIR / source_cfg["source_statement"]
    # Technical context for debugging imports — default console stays on INFO summaries below.
    logger.debug(
        "Pipeline run — source=%s model=%s path=%s",
        source_key,
        config.LLM_MODEL,
        resolved_input,
    )

    t0 = time.time()

    # ── Stage 1: Parse ──────────────────────────────────────────────
    logger.debug("[1/5] Parsing…")
    parsed = parser.parse(resolved_input)
    logger.debug("[1/5] Parsed %d row(s)", len(parsed))

    # ── Stage 2: Transform ──────────────────────────────────────────
    logger.debug("[2/5] Transforming…")
    canonical = transform(
        parsed,
        account_id=source_cfg["account_id"],
        currency=source_cfg.get("currency", "INR"),
        source_statement=source_cfg["source_statement"],
    )
    logger.debug("[2/5] Normalised %d transaction(s)", len(canonical))

    # ── Stage 3: Rules classify ─────────────────────────────────────
    logger.debug("[3/5] Applying sorting rules…")
    if write_to_csv:
        from pipeline.user_config import default_user_classification_config

        _ucfg = default_user_classification_config()
    else:
        from api.database import get_engine
        from api.services.user_classification import pipeline_config_for_account_owner
        from sqlmodel import Session

        with Session(get_engine()) as _session:
            _ucfg = pipeline_config_for_account_owner(_session, source_cfg["account_id"])
    classify_rules(canonical, _ucfg)
    filled_type = sum(1 for t in canonical if t.txn_type)
    filled_ch = sum(1 for t in canonical if t.channel)
    logger.debug(
        "[3/5] Rules pass — type filled %d/%d · channel %d/%d",
        filled_type,
        len(canonical),
        filled_ch,
        len(canonical),
    )

    # ── Stage 4: Smart labels (optional model) ──────────────────────
    logger.debug("[4/5] Auto-labelling (model=%s)…", config.LLM_MODEL)
    classify_llm(canonical)
    filled_type = sum(1 for t in canonical if t.txn_type)
    filled_cp = sum(1 for t in canonical if t.counterparty)
    filled_cat = sum(1 for t in canonical if t.counterparty_category)
    logger.debug(
        "[4/5] Labels — type %d/%d · named %d/%d · category %d/%d",
        filled_type,
        len(canonical),
        filled_cp,
        len(canonical),
        filled_cat,
        len(canonical),
    )

    # ── Stage 5: Write ──────────────────────────────────────────────
    run = None
    csv_path: Path | None = None
    if write_to_csv:
        csv_path = Path(output_file or config.OUTPUT_DIR / f"transactions_{source_key}.csv")
        logger.debug("[5/5] Writing CSV → %s", csv_path)
        write_csv(canonical, csv_path)
    else:
        logger.debug("[5/5] Saving to your Arth database…")
        from api.database import get_engine
        from pipeline.db_writer import write_to_db
        from sqlmodel import Session

        with Session(get_engine()) as session:
            run = write_to_db(
                canonical,
                source_key=source_key,
                llm_model=config.LLM_MODEL,
                session=session,
            )
        logger.debug(
            "[5/5] DB write — %d new · %d updated · %d total · run id=%s",
            run.new_count,
            run.updated_count,
            run.txn_count,
            run.id,
        )

    elapsed = time.time() - t0
    labelled, needs_review = _labelling_counts(canonical)

    if write_to_csv:
        assert csv_path is not None
        logger.info(
            "Statement import finished — %d transactions in %.1fs (%d labelled · %d may need a quick look in Review) · saved %s",
            len(canonical),
            elapsed,
            labelled,
            needs_review,
            csv_path.name,
        )
    else:
        assert run is not None
        logger.info(
            "Statement import finished — %d transactions saved in %.1fs (%d new · %d updated · %d labelled · %d may need a quick look in Review)",
            run.txn_count,
            elapsed,
            run.new_count,
            run.updated_count,
            labelled,
            needs_review,
        )

    return canonical


def main(argv: list[str] | None = None) -> None:
    # Initialise logging as the very first thing so every subsequent log line
    # (including from imported modules) gets proper formatting.
    setup_logging()

    args = _parse_args(argv)

    if args.llm:
        config.LLM_MODEL = args.llm

    write_to_csv = args.csv

    if args.all_sources:
        user_id = _pipeline_cli_user_id()
        from api.database import get_engine, init_db
        from sqlmodel import Session

        init_db()
        with Session(get_engine()) as _s:
            all_keys = sorted(config.get_source_configs(user_id, _s).keys())
        logger.info(
            "Importing every configured statement source — %d in this run.",
            len(all_keys),
        )
        for i, source_key in enumerate(all_keys, 1):
            logger.debug("Source %d/%d: %s", i, len(all_keys), source_key)
            _run_single_source(source_key, write_to_csv=write_to_csv)
    else:
        canonical = _run_single_source(
            args.source,
            input_file=args.input,
            write_to_csv=write_to_csv,
            output_file=args.output,
        )

        # ── Optional: Validate ──────────────────────────────────────
        if args.validate:
            from pipeline.validator import print_report, validate
            benchmark = args.benchmark or config.GSHEET_BENCHMARK_FILE
            logger.debug("Validating output against benchmark file %s", benchmark)
            result = validate(canonical, benchmark)
            print_report(result)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Raw-to-canonical transaction pipeline",
    )
    p.add_argument(
        "--source", default="hdfc_savings",
        help="Source key (default: hdfc_savings)",
    )
    p.add_argument(
        "--all-sources", action="store_true",
        help="Run all sources from user_pipeline_sources for ARTH_USER_ID sequentially",
    )
    p.add_argument(
        "--input", type=str, default=None,
        help="Override input file path (single-source mode only)",
    )
    p.add_argument(
        "--output", type=str, default=None,
        help="Override output CSV path (requires --csv)",
    )
    p.add_argument(
        "--csv", action="store_true",
        help="Write to CSV instead of SQLite (legacy mode)",
    )
    p.add_argument(
        "--llm", type=str, default=None,
        help="Override LLM model (auto, none, or a specific model key)",
    )
    p.add_argument(
        "--validate", action="store_true",
        help="Run validator against GSheet benchmark after pipeline",
    )
    p.add_argument(
        "--benchmark", type=str, default=None,
        help="Override benchmark CSV for validation",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    main()
