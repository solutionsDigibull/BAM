"""E0.1 ADR coverage in CI: docs/adr/check_coverage.py on the real docs and on broken copies."""

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "docs/adr/check_coverage.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_e0_1_t01_t02_t03_t05_all_decisions_open_items_statuses_and_links() -> None:
    result = run()
    assert result.returncode == 0, result.stdout
    assert "55/55 decisions, 12/12 open items" in result.stdout


def broken_copy(tmp_path: Path) -> tuple[Path, Path]:
    shutil.copytree(REPO / "docs", tmp_path / "docs")  # whole tree: links must still resolve
    return tmp_path / "docs/adr", tmp_path / "docs/plan"


def test_e0_1_t04_a_missing_decision_fails_and_is_named(tmp_path: Path) -> None:
    adr_dir, plan_dir = broken_copy(tmp_path)
    adr = adr_dir / "0014-platform-and-infrastructure.md"
    text = adr.read_text(encoding="utf-8")
    adr.write_text(
        "\n".join(line for line in text.splitlines() if not line.startswith("| C-37 |")),
        encoding="utf-8",
    )
    result = run("--adr-dir", str(adr_dir), "--plan-dir", str(plan_dir))
    assert result.returncode == 1
    assert "missing: C-37" in result.stdout


def test_e0_1_t07_superseded_adr_hands_ownership_to_its_successor(tmp_path: Path) -> None:
    adr_dir, plan_dir = broken_copy(tmp_path)
    old = adr_dir / "0005-identity-and-tenancy.md"
    text = old.read_text(encoding="utf-8")
    # A new ADR repeats the rows; the old one changes only its Status line.
    new = adr_dir / "0099-identity-revisited.md"
    new.write_text(text.replace("# 0005", "# 0099") + "\n- Supersedes: 0005\n", encoding="utf-8")
    old.write_text(
        text.replace("- Status: Accepted", "- Status: Superseded by 0099"), encoding="utf-8"
    )
    readme = adr_dir / "README.md"
    index = readme.read_text(encoding="utf-8") + "\n[0099](0099-identity-revisited.md)\n"
    readme.write_text(index, encoding="utf-8")
    assert run("--adr-dir", str(adr_dir), "--plan-dir", str(plan_dir)).returncode == 0
    # Without the status change both would own C-03/C-04.
    old.write_text(text, encoding="utf-8")
    result = run("--adr-dir", str(adr_dir), "--plan-dir", str(plan_dir))
    assert result.returncode == 1 and "duplicated: C-03" in result.stdout


def test_e0_1_changed_decision_text_or_default_is_caught(tmp_path: Path) -> None:
    adr_dir, plan_dir = broken_copy(tmp_path)
    adr = adr_dir / "0010-isatvon-and-skill-manifest.md"
    text = adr.read_text(encoding="utf-8")
    dash = chr(0x2013)  # en dash, as in PLAN §30
    adr.write_text(text.replace(f"| 1{dash}10 |", f"| 1{dash}50 |"), encoding="utf-8")
    result = run("--adr-dir", str(adr_dir), "--plan-dir", str(plan_dir))
    assert result.returncode == 1 and "O-20 default differs" in result.stdout
