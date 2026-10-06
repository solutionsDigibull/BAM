#!/usr/bin/env python3
"""Print the runtime registry: every installed distribution, its version and its import names.

#67 extends the image with the allowed libraries; this script is what keeps the registry truthful.
"""

import json
from importlib.metadata import distributions, packages_distributions

FORBIDDEN = {"duckdb"}
TOOLING = {"pip", "setuptools", "wheel"}


def main() -> None:
    imports: dict[str, list[str]] = {}
    for module, dists in packages_distributions().items():
        for dist in dists:
            imports.setdefault(dist.lower(), []).append(module)
    entries = []
    for dist in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in TOOLING:
            continue
        entries.append(
            {
                "package": name,
                "version": dist.version,
                "import_names": sorted(imports.get(name.lower(), [])),
                "allowed": name.lower() not in FORBIDDEN,
            }
        )
    print(json.dumps({"packages": entries}, indent=2))


if __name__ == "__main__":
    main()
