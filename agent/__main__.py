"""Allow ``python -m agent`` as an alias for the CLI REPL."""

from __future__ import annotations

from agent.cli import main

if __name__ == "__main__":
    main()
