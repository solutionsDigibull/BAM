"""WG-6 (#754): URLs read from the free text of a master cell. Pure, no network, no database."""

import pytest
from sbam_api.repo_catalog.parse import candidate_id, parse_cell, safe_value
from sbam_contracts.repo_catalog import RejectReason


def urls(text: str) -> list[str | None]:
    return [f.url for f in parse_cell(text)]


def test_wg6_t01_one_url() -> None:
    [one] = parse_cell("https://github.com/acme/pcb-tools")
    assert (one.url, one.ref, one.reason) == ("https://github.com/acme/pcb-tools", None, None)


def test_wg6_t01_urls_are_found_inside_prose_and_lists() -> None:
    text = (
        "Primary: https://github.com/acme/a, fallback https://gitlab.com/acme/b.\n"
        "see (https://codeberg.org/acme/c) for the old one"
    )
    assert urls(text) == [
        "https://github.com/acme/a",
        "https://gitlab.com/acme/b",
        "https://codeberg.org/acme/c",
    ]


def test_wg6_t01_notes_are_the_text_around_the_url() -> None:
    [one] = parse_cell("Costing engine - https://github.com/acme/cost - MIT")
    assert one.notes == "Costing engine - - MIT"


def test_wg6_t01_a_branch_or_tag_follows_a_hash() -> None:
    [one] = parse_cell("https://github.com/acme/a#v1.2/rc")
    assert (one.url, one.ref) == ("https://github.com/acme/a", "v1.2/rc")


def test_wg6_t01_the_same_url_twice_is_one_candidate_and_dot_git_is_dropped() -> None:
    assert urls("https://github.com/a/b.git https://github.com/a/b/") == ["https://github.com/a/b"]


def test_wg6_t01_a_bare_host_and_path_is_read_as_https() -> None:
    assert urls("github.com/acme/a") == ["https://github.com/acme/a"]


@pytest.mark.parametrize("text", ["", "TBD", "internal tool, no repo", "v1.2/foo 3.4"])
def test_wg6_t01_text_without_a_url_gives_nothing(text: str) -> None:
    assert parse_cell(text) == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("http://github.com/acme/a", RejectReason.NOT_HTTPS),
        ("ssh://git@github.com/acme/a.git", RejectReason.NOT_HTTPS),
        ("git@github.com:acme/a.git", RejectReason.NOT_HTTPS),
        ("file:///etc/passwd", RejectReason.NOT_HTTPS),
        ("ftp://example.com/x", RejectReason.NOT_HTTPS),
        ("https://user:secret@github.com/acme/a", RejectReason.HAS_CREDENTIALS),
        ("https://ghp_abc123@github.com/acme/a", RejectReason.HAS_CREDENTIALS),
        ("https://github.com/acme/a?token=abc", RejectReason.HAS_QUERY),
    ],
)
def test_wg6_t02_unsafe_urls_are_rejected_not_fetched(text: str, reason: RejectReason) -> None:
    [one] = parse_cell(text)
    assert (one.url, one.reason) == (None, reason)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.21",
        "172.16.0.9",
        "169.254.169.254",
        "[::1]",
        "2130706433",  # 127.0.0.1 written as one number
        "0x7f000001",
        "0177.0.0.1",
        "git.corp.local",
        "gitea.internal",
        "gitserver",  # no dot: a private host name
        "8.8.8.8",  # an IP literal is never a repository host
    ],
)
def test_wg6_t02_private_and_address_hosts_are_rejected(host: str) -> None:
    [one] = parse_cell(f"https://{host}/acme/a")
    assert (one.url, one.reason) == (None, RejectReason.PRIVATE_ADDRESS)


def test_wg6_t02_a_credential_never_reaches_the_shown_value() -> None:
    [one] = parse_cell("https://user:secret@github.com/acme/a")
    assert "secret" not in one.value and "user" not in one.value
    assert "secret" not in safe_value("ssh://git:secret@host/x")


def test_wg6_a_rejection_keeps_the_good_urls_around_it() -> None:
    found = parse_cell("http://old.example.com/a https://github.com/acme/ok")
    assert [(f.url, f.reason) for f in found] == [
        (None, RejectReason.NOT_HTTPS),
        ("https://github.com/acme/ok", None),
    ]


def test_wg6_ids_are_stable_and_depend_on_the_row_and_the_url() -> None:
    a = candidate_id("e1", "ADMIN_AGENTS", "https://github.com/a/b")
    assert a == candidate_id("e1", "ADMIN_AGENTS", "https://github.com/a/b")
    assert a != candidate_id("e2", "ADMIN_AGENTS", "https://github.com/a/b")
    assert a != candidate_id("e1", "ADMIN_AGENTS", "https://github.com/a/c")
    assert a != candidate_id("e1", "ADMIN_TOOLING", "https://github.com/a/b")
