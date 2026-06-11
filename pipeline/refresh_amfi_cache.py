"""CLI: download AMFI NAVAll and rebuild ``data/.amfi_cache/isin_to_scheme.json``."""

from __future__ import annotations

import argparse
import logging

from pipeline.amfi_isin_map import refresh_amfi_isin_cache

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    p = argparse.ArgumentParser(description="Refresh AMFI NAVAll cache and ISIN→scheme map.")
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download NAVAll even if the file is fresh.",
    )
    p.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Treat NAVAll older than this many hours as stale (default: 24).",
    )
    args = p.parse_args()
    data = refresh_amfi_isin_cache(force=args.force, max_age_hours=args.max_age_hours)
    print(f"AMFI ISIN map: {len(data)} entries")


if __name__ == "__main__":
    main()
