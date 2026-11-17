"""Static checks on repository text: Bandit on the Python files, and a pattern scan on every text
file.

Nothing is executed. Bandit reads the files on a scratch copy (the existing `run_bandit`); the
pattern
scan is regular expressions over the bytes. A finding never contains the matched secret.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from sbam_contracts.repo_scan import Severity, StaticFinding

from sbam_workers.build.gates.static_tools import run_bandit
from sbam_workers.build.gates.toolchain import Executor

MAX_SCANNED_BYTES = (
    1024 * 1024
)  # a larger file is skipped by the pattern scan (Bandit still reads .py)
MAX_FINDINGS = 200


@dataclass(frozen=True)
class Pattern:
    rule: str
    regex: re.Pattern[str]
    severity: Severity
    message: str
    only: re.Pattern[str] | None = None  # a file name the rule applies to


PATTERNS = (
    Pattern(
        "PIPE_TO_SHELL",
        re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"),
        Severity.HIGH,
        "downloads and runs a script",
    ),
    Pattern(
        "EXEC_DECODED",
        re.compile(
            r"\b(eval|exec)\s*\(\s*(base64\.b64decode|codecs\.decode|bytes\.fromhex|zlib\.decompress)"
        ),
        Severity.HIGH,
        "runs decoded data",
    ),
    Pattern(
        "NPM_INSTALL_SCRIPT",
        re.compile(r'"(preinstall|install|postinstall|prepare)"\s*:'),
        Severity.MEDIUM,
        "runs a script when the package is installed",
        only=re.compile(r"(^|/)package\.json$"),
    ),
    Pattern(
        "SETUP_RUNS_CODE",
        re.compile(r"\b(os\.system|subprocess\.\w+|urlopen|requests\.(get|post))\s*\("),
        Severity.MEDIUM,
        "setup.py runs code or reaches the network when installed",
        only=re.compile(r"(^|/)setup\.py$"),
    ),
    Pattern(
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
        Severity.HIGH,
        "a private key is committed",
    ),
    Pattern(
        "AWS_KEY_ID",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        Severity.HIGH,
        "an AWS access key id is committed",
    ),
    Pattern(
        "GITHUB_TOKEN",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        Severity.HIGH,
        "a GitHub token is committed",
    ),
)


def pattern_scan(files: Mapping[str, bytes]) -> list[StaticFinding]:
    found: list[StaticFinding] = []
    for path, data in sorted(files.items()):
        if len(data) > MAX_SCANNED_BYTES or b"\0" in data[:2048]:
            continue  # too large, or binary (ClamAV reads those)
        text = data.decode("utf-8", "replace")
        for pattern in PATTERNS:
            if pattern.only and not pattern.only.search(path):
                continue
            for match in pattern.regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(
                    StaticFinding(
                        tool="pattern",
                        rule=pattern.rule,
                        path=path[:300],
                        line=line,
                        severity=pattern.severity,
                        message=pattern.message,
                    )
                )
                if len(found) >= MAX_FINDINGS:
                    return found
    return found


def bandit_scan(
    files: Mapping[str, bytes], executor: Executor | None = None
) -> list[StaticFinding]:
    """Bandit's findings. A tool that cannot run raises `ToolFailure` (retried, never a pass)."""
    result = run_bandit(files, executor)
    return [
        StaticFinding(
            tool="bandit",
            rule=str(f["code"]),
            path=str(f["file"])[:300],
            line=int(f["line"] or 0),
            severity=Severity(str(f["severity"])),
            message=str(f["message"])[:300],
        )
        for f in result.findings
    ]
