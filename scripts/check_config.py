"""
Entrypoint: validate config/*.yaml without running anything else.

`load_config()` already validates on every real run (run_paper, run_telegram,
etc.), but a misconfiguration there surfaces as a raw Python traceback buried
under whatever else that script does first. This is the same validation,
standalone, with a clean pass/fail report -- the thing to run right after
editing config/*.yaml, or in CI before merging a config change.

    python -m scripts.check_config [--config-dir path/to/config]

Exit code: 0 valid, 1 invalid or unreadable. Reads local YAML only -- no
credentials, no network, no trading logic.
"""

from __future__ import annotations

import argparse
import sys

from src.common.config import load_config
from src.common.config_schema import ConfigError


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate config/*.yaml.")
    parser.add_argument("--config-dir", default=None,
                        help="alternate config directory (default: ./config)")
    args = parser.parse_args()

    load_config.cache_clear()
    try:
        config = load_config(args.config_dir)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        print(f"config unreadable: {exc}", file=sys.stderr)
        return 1

    n_symbols = len(config.enabled_symbols())
    n_strategies = len(config.strategies.get("strategies", {}))
    print(f"config valid -- mode={config.settings.get('mode', 'paper')}, "
          f"{n_symbols} enabled symbol(s), {n_strategies} strategy block(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
