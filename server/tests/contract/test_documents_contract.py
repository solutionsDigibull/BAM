"""E4.2 contract tests: the upload request and token models (TD §8.5)."""

import pytest
from pydantic import ValidationError
from sbam_contracts.documents import (
    MAX_ACTIVE_FILES,
    MAX_FILE_BYTES,
    UPLOAD_TOKEN_TTL_SECONDS,
    UploadRequest,
    UploadToken,
)


def one_file(**file: object) -> dict[str, object]:
    return {"name": "spec.pdf", "size": 2_000_000, "mime": "application/pdf", **file}


def request(**file: object) -> dict[str, object]:
    return {"files": [one_file(**file)]}


def error_paths(payload: dict[str, object]) -> list[tuple[object, ...]]:
    with pytest.raises(ValidationError) as caught:
        UploadRequest.model_validate(payload)
    return [error["loc"] for error in caught.value.errors()]


def test_e4_2_limits_are_the_documented_numbers() -> None:
    assert MAX_FILE_BYTES == 15_728_640
    assert MAX_ACTIVE_FILES == 20
    assert UPLOAD_TOKEN_TTL_SECONDS == 900


def test_e4_2_t01_valid_request_is_accepted() -> None:
    assert UploadRequest.model_validate(request()).files[0].name == "spec.pdf"


def test_e4_2_t03_exactly_15_mb_is_accepted() -> None:
    assert UploadRequest.model_validate(request(size=15_728_640)).files[0].size == 15_728_640


@pytest.mark.parametrize("size", [0, -1, 15_728_641])
def test_e4_2_t02_size_outside_1_to_15_mb_points_at_the_size(size: int) -> None:
    assert error_paths(request(size=size)) == [("files", 0, "size")]


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd.txt",
        "..\\..\\boot.ini",
        "dir/file.txt",
        "a\x00b.txt",
        "tab\tname.txt",
        "..",
        ".",
        "",
        "   ",
        "x" * 256,
    ],
)
def test_e4_2_t09_bad_names_point_at_files_0_name(name: str) -> None:
    assert error_paths(request(name=name)) == [("files", 0, "name")]


def test_e4_2_name_of_255_characters_is_accepted() -> None:
    assert len(UploadRequest.model_validate(request(name="x" * 255)).files[0].name) == 255


def test_e4_2_name_is_trimmed() -> None:
    assert UploadRequest.model_validate(request(name="  spec.pdf ")).files[0].name == "spec.pdf"


def test_e4_2_files_must_be_1_to_20() -> None:
    assert error_paths({"files": []}) == [("files",)]
    assert error_paths({"files": [one_file()] * (MAX_ACTIVE_FILES + 1)}) == [("files",)]
    ok = UploadRequest.model_validate({"files": [one_file()] * MAX_ACTIVE_FILES})
    assert len(ok.files) == MAX_ACTIVE_FILES


def test_e4_2_unknown_fields_are_rejected() -> None:
    assert error_paths(request(storage_key="x/y")) == [("files", 0, "storage_key")]


def test_e4_2_size_must_be_an_integer() -> None:
    assert error_paths(request(size=1.5)) == [("files", 0, "size")]


def test_e4_2_token_response_shape() -> None:
    token = UploadToken.model_validate(
        {
            "token": "t",
            "tus_endpoint": "http://localhost:1080/files/",
            "expires_at": "2026-10-14T10:15:00Z",
            "accepted": [{"name": "a.pdf", "size": 1, "mime": "application/pdf"}],
            "rejected": [{"name": "setup.exe", "code": "UNSUPPORTED_TYPE"}],
        }
    )
    assert token.rejected[0].code == "UNSUPPORTED_TYPE"
    with pytest.raises(ValidationError):
        UploadToken.model_validate({**token.model_dump(), "rejected": [{"name": "x", "code": "?"}]})
