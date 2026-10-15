"""`python -m sbam_workers.build.codegen <name> <version>`: print a template's tree hash."""

import sys

from sbam_workers.build.codegen.store import main

if __name__ == "__main__":  # importable without side effects (module walkers import every file)
    sys.exit(main(sys.argv[1:]))
