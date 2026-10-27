"""The issue numbers a PR body closes with GitHub's keywords (#244).

GitHub only acts on `Closes #N` when a PR merges into the default branch (`main`); SBAM merges
into `dev`, so `.github/workflows/close-issues.yml` runs this on every merge into `dev` and closes
what it prints. Same keywords as GitHub: close(s|d), fix(es|ed), resolve(s|d), optional colon,
then `#N` in this repository. `owner/repo#N`, `Part of #N` and anything inside code are ignored.

    PR_BODY="Closes #12, fixes #13" python3 scripts/closing_issues.py   # prints 12 and 13
"""

import os
import re

FENCE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
INLINE = re.compile(r"`[^`\n]*`")
KEYWORD = re.compile(
    r"(?<![\w/])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b",
    re.IGNORECASE,
)


def closing_issues(body: str) -> list[int]:
    text = INLINE.sub(" ", FENCE.sub(" ", body or ""))
    found: list[int] = []
    for match in KEYWORD.finditer(text):
        number = int(match.group(1))
        if number not in found:
            found.append(number)
    return found


def main() -> None:
    for number in closing_issues(os.environ.get("PR_BODY", "")):
        print(number)


if __name__ == "__main__":
    main()
