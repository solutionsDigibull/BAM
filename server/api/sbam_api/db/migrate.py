"""`python -m sbam_api.db.migrate [alembic args]`: Alembic without an ini file.

The api-migrate job runs it with no args (= `upgrade head`). It works from the installed package,
so the prod image needs nothing but the wheel. Developers use the same entry point:
`uv run python -m sbam_api.db.migrate revision --autogenerate -m "add projects"`, `heads`, `check`.
"""

import sys
from pathlib import Path

from alembic.config import CommandLine, Config

from sbam_api.core.logging import configure_logging

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return cfg  # the URL comes from DATABASE_URL (env.py)


def main(argv: list[str] | None = None) -> None:
    args = (sys.argv[1:] if argv is None else argv) or ["upgrade", "head"]
    configure_logging()  # JSON lines, so Alembic's "Running upgrade ..." shows in the job log
    cli = CommandLine(prog="sbam-migrate")
    options = cli.parser.parse_args(args)
    if not hasattr(options, "cmd"):
        cli.parser.error("too few arguments")
    cli.run_cmd(config(), options)


if __name__ == "__main__":
    main()
