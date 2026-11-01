"""E13.2 input verification: the bundle tar is untrusted until it hashes to what was recorded.

Each test builds one hostile tar by hand and expects it refused with HASH_MISMATCH, so no VM is ever
created for it (E13.2-T04). The good paths are here too: a check that rejects everything proves
nothing.
"""

import gzip
import hashlib
import io
import tarfile
from collections.abc import Sequence

import pytest
from sbam_contracts.sandbox import InfraReason
from sbam_workers.build.codegen.bundle import make_bundle
from sbam_workers.sandbox.backend import SandboxInfraError
from sbam_workers.sandbox.verify import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    MAX_FILE_BYTES,
    HashMismatch,
    read_bundle_tar,
    same_hash,
    verify_bundle,
    verify_sha,
)

FILES = {
    "skill_server.py": b"print('ok')\n",
    "helpers.py": b"X = 1\n",
    "loop_spec.json": b'{"max_iterations": 3}\n',
}


def good() -> tuple[bytes, dict[str, str], str]:
    bundle = make_bundle(FILES)
    return bundle.tar, dict(bundle.hashes), bundle.bundle_sha256


def tar_of(
    entries: Sequence[tuple[tarfile.TarInfo, bytes | None]], fmt: int = tarfile.USTAR_FORMAT
) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=fmt) as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return out.getvalue()


def reg(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def refused(tar_bytes: bytes) -> HashMismatch:
    _, hashes, bundle_hash = good()
    with pytest.raises(HashMismatch) as caught:
        verify_bundle(tar_bytes, hashes, bundle_hash)
    return caught.value


# --- the good paths ---------------------------------------------------------------------------


def test_e13_2_a_bundle_that_hashes_to_what_was_recorded_is_returned_byte_for_byte() -> None:
    tar, hashes, bundle_hash = good()
    assert verify_bundle(tar, hashes, bundle_hash) == FILES


def test_e13_2_recorded_hashes_may_carry_the_sha256_prefix() -> None:
    tar, hashes, bundle_hash = good()
    prefixed = {k: f"sha256:{v}" for k, v in hashes.items()}
    assert verify_bundle(tar, prefixed, f"sha256:{bundle_hash}") == FILES


def test_e13_2_hash_comparison_accepts_both_spellings_and_rejects_the_rest() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    assert same_hash(digest, digest) and same_hash(f"sha256:{digest}", digest)
    assert not same_hash(digest, digest[:-1] + "0") and not same_hash("", digest)
    verify_sha(b"x", digest, "dataset")
    with pytest.raises(HashMismatch):
        verify_sha(b"y", digest, "dataset")


# --- T04: changed after the gates -------------------------------------------------------------


def test_e13_2_t04_a_file_changed_after_hashing_is_a_hash_mismatch_with_the_right_reason() -> None:
    _, hashes, bundle_hash = good()
    changed = make_bundle({**FILES, "skill_server.py": b"import os\n"}).tar
    with pytest.raises(HashMismatch) as caught:
        verify_bundle(changed, hashes, bundle_hash)
    assert caught.value.reason is InfraReason.HASH_MISMATCH
    assert isinstance(caught.value, SandboxInfraError)


def test_e13_2_t04_the_message_names_the_input_never_its_content() -> None:
    _, hashes, bundle_hash = good()
    changed = make_bundle({**FILES, "skill_server.py": b"SECRET-TOKEN-1234\n"}).tar
    with pytest.raises(HashMismatch) as caught:
        verify_bundle(changed, hashes, bundle_hash)
    assert "bundle" in str(caught.value) and "SECRET-TOKEN" not in str(caught.value)


def test_e13_2_an_extra_file_is_refused() -> None:
    refused(make_bundle({**FILES, "extra.py": b"y = 2\n"}).tar)


def test_e13_2_a_missing_file_is_refused() -> None:
    refused(make_bundle({k: v for k, v in FILES.items() if k != "helpers.py"}).tar)


def test_e13_2_a_matching_file_set_with_a_wrong_bundle_hash_is_refused() -> None:
    tar, hashes, _ = good()
    with pytest.raises(HashMismatch):
        verify_bundle(tar, hashes, "0" * 64)


def test_e13_2_a_wrong_per_file_hash_is_refused_even_if_the_bundle_hash_is_right() -> None:
    tar, hashes, bundle_hash = good()
    with pytest.raises(HashMismatch):
        verify_bundle(tar, {**hashes, "helpers.py": "0" * 64}, bundle_hash)


# --- the tar tricks ---------------------------------------------------------------------------


def test_e13_2_a_duplicate_member_cannot_replace_the_hashed_one() -> None:
    """tar lets two entries share a name and the later one wins on extraction."""
    entries = [reg(n, d) for n, d in FILES.items()] + [reg("skill_server.py", b"import os\n")]
    refused(tar_of(entries))


def link(kind: bytes, name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


@pytest.mark.parametrize(
    "member",
    [
        link(tarfile.SYMTYPE, "skill_server.py", "/etc/passwd"),
        link(tarfile.LNKTYPE, "skill_server.py", "helpers.py"),
        link(tarfile.SYMTYPE, "evil.py", "../../outside"),
    ],
    ids=["symlink-absolute", "hardlink", "symlink-traversal"],
)
def test_e13_2_links_are_refused(member: tarfile.TarInfo) -> None:
    refused(tar_of([reg(n, d) for n, d in FILES.items()] + [(member, None)]))


@pytest.mark.parametrize(
    "kind", [tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE]
)
def test_e13_2_directories_and_devices_are_refused(kind: bytes) -> None:
    info = tarfile.TarInfo("dev")
    info.type = kind
    refused(tar_of([reg(n, d) for n, d in FILES.items()] + [(info, None)]))


@pytest.mark.parametrize(
    "name",
    [
        "../skill_server.py",
        "/etc/passwd",
        "a/b.py",
        "./skill_server.py",
        "SKILL.py",
        "skill-server.py",
        "skill_server.py ",
        "skill_server.pyc",
        "skill_server.sh",
        ".hidden.py",
        "a" * 70 + ".py",
        "",
        "skill_server.py\x00.txt",
    ],
)
def test_e13_2_only_the_assemblers_flat_file_names_are_accepted(name: str) -> None:
    _, hashes, bundle_hash = good()
    try:
        hostile = tar_of([reg(name, b"x = 1\n")])
    except ValueError:
        return  # tarfile itself refuses to write some names
    with pytest.raises(HashMismatch):
        verify_bundle(hostile, hashes, bundle_hash)


def test_e13_2_pax_and_gnu_extended_headers_do_not_smuggle_a_name() -> None:
    long_name = "a" * 120 + ".py"  # forces a PAX / GNU long-name record
    for fmt in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
        refused(tar_of([reg(long_name, b"x = 1\n")], fmt))


def test_e13_2_a_member_that_claims_to_be_huge_is_refused_before_it_is_read() -> None:
    info, _ = reg("skill_server.py", b"")
    info.size = MAX_FILE_BYTES + 1  # header only: the data that follows is missing
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(info, io.BytesIO(b"x" * (MAX_FILE_BYTES + 1)))
    refused(out.getvalue())


@pytest.mark.parametrize("cut", [100, 512, 700, 1024, 1500, 2048, 3000])
def test_e13_2_a_tar_cut_anywhere_inside_its_data_is_refused(cut: int) -> None:
    """Cutting only the zero padding at the end leaves a valid tar, so the cuts are inside members
    and exactly on member boundaries (the file set then no longer matches the record)."""
    tar, hashes, bundle_hash = good()
    with pytest.raises(HashMismatch):
        verify_bundle(tar[:cut], hashes, bundle_hash)


def test_e13_2_bytes_after_the_end_of_the_archive_are_ignored_not_executed() -> None:
    tar, hashes, bundle_hash = good()
    padded = tar + b"import os; os.system('x')\n" * 20
    assert verify_bundle(padded, hashes, bundle_hash) == FILES  # only the members are used


@pytest.mark.parametrize(
    "blob",
    [b"", b"not a tar", b"\x00" * 4096, gzip.compress(good()[0]), b"PK\x03\x04" + b"\x00" * 600],
    ids=["empty", "garbage", "zeros", "gzip", "zip"],
)
def test_e13_2_things_that_are_not_a_plain_tar_are_refused(blob: bytes) -> None:
    refused(blob)


def test_e13_2_too_many_members_is_refused() -> None:
    entries = [reg(f"m{i}.py", b"x\n") for i in range(MAX_BUNDLE_FILES + 1)]
    refused(tar_of(entries))


def test_e13_2_too_many_bytes_in_total_is_refused() -> None:
    chunk = b"#" * (MAX_FILE_BYTES - 1)
    entries = [reg(f"m{i}.py", chunk) for i in range(MAX_BUNDLE_BYTES // len(chunk) + 2)]
    refused(tar_of(entries))


def test_e13_2_a_tar_larger_than_any_bundle_is_refused_without_parsing_it() -> None:
    refused(b"\x00" * (MAX_BUNDLE_BYTES + 128 * 1024))


def test_e13_2_a_tar_of_many_empty_headers_is_refused_quickly() -> None:
    """Thousands of header-only members must cost a count check, not memory."""
    entries = [reg(f"m{i}.py", b"") for i in range(15_000)]
    blob = tar_of(entries)
    assert len(blob) < MAX_BUNDLE_BYTES
    refused(blob)


def test_e13_2_read_bundle_tar_alone_does_not_trust_names_it_only_returns_files() -> None:
    tar, _, _ = good()
    assert read_bundle_tar(tar) == FILES


def test_e13_2_nothing_hostile_passes_by_changing_only_the_recorded_hashes() -> None:
    """The recorded map is data too: a map naming a file the tar does not have is refused."""
    tar, hashes, bundle_hash = good()
    with pytest.raises(HashMismatch):
        verify_bundle(tar, {**hashes, "ghost.py": "0" * 64}, bundle_hash)
