"""E4.2 unit tests: the extension allowlist and the signed upload token."""

import time
import uuid

import pytest
from sbam_api.ingest import tokens
from sbam_api.ingest.rules import ALLOWED_EXTENSIONS, extension, is_allowed

KEY = "unit-test-key"
CLAIMS = tokens.UploadClaims(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 2_000_000_000)


def test_e4_2_allowlist_is_the_td_5_2_mvp_set() -> None:
    assert ALLOWED_EXTENSIONS == {
        "md", "txt", "eml", "msg", "json", "xml", "yaml", "yml", "py", "sql", "js", "go", "cpp",
        "csv", "xlsx", "docx", "pdf", "zip", "tar.gz", "png", "jpg", "jpeg", "tiff",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("spec.pdf", "pdf"),
        ("SPEC.PDF", "pdf"),
        ("a.b.docx", "docx"),
        ("logs.tar.gz", "tar.gz"),
        ("LOGS.TAR.GZ", "tar.gz"),
        ("noextension", ""),
        (".gitignore", ""),
        ("trailing.", ""),
        ("", ""),
    ],
)
def test_e4_2_extension(name: str, expected: str) -> None:
    assert extension(name) == expected


@pytest.mark.parametrize(
    "name", ["setup.exe", "drawing.dwg", "board.step", "movie.mp4", "x", "a.gz"]
)
def test_e4_2_types_that_are_not_mvp_are_refused(name: str) -> None:
    assert not is_allowed(name)


def test_e4_2_token_round_trip() -> None:
    assert tokens.verify(KEY, tokens.issue(KEY, CLAIMS), now=1.0) == CLAIMS


def test_e4_2_token_expires_but_post_finish_can_ignore_that() -> None:
    token = tokens.issue(KEY, CLAIMS)
    late = CLAIMS.expires_at + 1
    with pytest.raises(tokens.TokenError):
        tokens.verify(KEY, token, now=late)
    assert tokens.verify(KEY, token, now=late, check_expiry=False) == CLAIMS


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t[:-2],
        lambda t: t + "x",
        lambda t: t.replace(".", ""),
        lambda t: "a." + t.split(".")[1],
    ],
)
def test_e4_2_tampered_token_is_refused(mangle: object) -> None:
    token = tokens.issue(KEY, CLAIMS)
    with pytest.raises(tokens.TokenError):
        tokens.verify(KEY, mangle(token), now=1.0)  # type: ignore[operator]


def test_e4_2_token_from_another_key_is_refused() -> None:
    with pytest.raises(tokens.TokenError):
        tokens.verify("other-key", tokens.issue(KEY, CLAIMS), now=1.0)


def test_e4_2_token_defaults_to_the_real_clock() -> None:
    soon = tokens.UploadClaims(*CLAIMS.__dict__.values())
    assert tokens.verify(KEY, tokens.issue(KEY, soon)) == soon
    gone = tokens.UploadClaims(
        CLAIMS.tenant_id, CLAIMS.project_id, CLAIMS.user_id, CLAIMS.batch_id, int(time.time()) - 1
    )
    with pytest.raises(tokens.TokenError):
        tokens.verify(KEY, tokens.issue(KEY, gone))
