# /// script
# requires-python = ">=3.11"
# ///
"""Fail when tests that CI must run were skipped (or never collected).

Some tests skip themselves when an optional dependency is missing (a scratch PostgreSQL, `fastmcp`).
A skip is green, so a CI job that loses the dependency keeps passing while checking nothing (#476).
This reads a pytest JUnit report and requires every named test module to have run at least one test
and to have skipped none.

Usage: uv run scripts/ci_assert_ran.py reports/server-unit.xml test_e9_4_schema_sql [module ...]
"""

import sys
import xml.etree.ElementTree as ET


def check(report: str, modules: list[str]) -> list[str]:
    cases = ET.parse(report).getroot().iter("testcase")  # noqa: S314 - our own CI report
    ran: dict[str, int] = dict.fromkeys(modules, 0)
    problems: list[str] = []
    for case in cases:
        classname = case.get("classname", "")
        for module in modules:
            if classname.split(".")[-1] == module or classname.split(".")[-2:-1] == [module]:
                ran[module] += 1
                if case.find("skipped") is not None:
                    problems.append(f"{module}: {case.get('name')} was skipped")
    problems += [f"{module}: no test ran" for module, count in ran.items() if count == 0]
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    problems = check(argv[1], argv[2:])
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
