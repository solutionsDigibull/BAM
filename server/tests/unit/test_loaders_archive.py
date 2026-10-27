"""E4.7 loaders for `.zip` and `.tar.gz`: the members taken, the members refused, the limits."""

import gzip
import io
import os
import tarfile
import time
import tracemalloc
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from fixtures import fixture_path
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_ARCHIVE_MEMBERS,
    ParsedDocument,
    RejectedMember,
)
from sbam_workers.ingest.loaders import Parsed, ParseError, loader_for, no_heartbeat

MB = 1024 * 1024


def run(kind: str, data: bytes) -> Parsed:
    loader = loader_for(kind)
    assert loader is not None
    return loader(data, no_heartbeat)


def refused(kind: str, data: bytes) -> ErrorCode:
    with pytest.raises(ParseError) as caught:
        run(kind, data)
    return caught.value.code


def make_zip(
    members: dict[str, bytes],
    compression: int = zipfile.ZIP_DEFLATED,
    flags: dict[str, int] | None = None,
) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            info.flag_bits |= (flags or {}).get(name, 0)
            archive.writestr(info, data)
    return out.getvalue()


def mark_encrypted(data: bytes, name: str) -> bytes:
    """The zip with the encrypted flag set on one member (the data is left as it was: the loader
    must refuse the member without reading it)."""
    out = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        offset = archive.getinfo(name).header_offset
    out[offset + 6] |= 0x1  # general purpose flags of the local header
    entry = bytes(out).find(b"PK\x01\x02", 0)
    while entry != -1:
        if bytes(out[entry + 46 : entry + 46 + len(name)]) == name.encode():
            out[entry + 8] |= 0x1  # and of the central directory entry
        entry = bytes(out).find(b"PK\x01\x02", entry + 4)
    return bytes(out)


def make_tar(add: Callable[[tarfile.TarFile], None]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        add(archive)
    return gzip.compress(raw.getvalue())


def tar_file(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def targz(members: dict[str, bytes]) -> bytes:
    def add(archive: tarfile.TarFile) -> None:
        for name, data in members.items():
            tar_file(archive, name, data)

    return make_tar(add)


def taken(parsed: Parsed) -> list[str]:
    return [a.path for a in parsed.attachments]


def rejected(parsed: Parsed) -> list[tuple[str, ErrorCode]]:
    assert parsed.report.archive is not None
    return [(m.path, m.code) for m in parsed.report.archive.rejected]


KINDS = [("zip", make_zip), ("tar.gz", targz)]


# --- the fixtures and the report --------------------------------------------------------------


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_e4_7_t01_the_members_of_the_fixture_archives_are_taken(kind: str) -> None:
    name = "sample.zip" if kind == "zip" else "sample.tar.gz"
    parsed = run(kind, fixture_path("documents", name).read_bytes())
    assert taken(parsed) == ["rfq_summary.md", "bom_extract.csv"]
    assert parsed.report.archive is not None
    assert (parsed.report.archive.members_total, parsed.report.archive.accepted) == (2, 2)
    assert parsed.report.archive.rejected == []
    first = parsed.attachments[0]
    assert first.name == "rfq_summary.md" and first.content_type == "text/markdown"
    assert len(first.data) == 233 and first.data.startswith(b"# ")
    text = parsed.markdown
    assert isinstance(text, str) and "- rfq_summary.md (233 bytes)" in text
    ParsedDocument(document_version_id=uuid.uuid4(), loader="archive", blocks=list(parsed.blocks))


@pytest.mark.parametrize(("kind", "build"), KINDS)
def test_e4_7_members_in_folders_keep_their_path_and_their_name(kind: str, build: Callable) -> None:  # type: ignore[type-arg]
    parsed = run(kind, build({"reports/q3/summary.md": b"# Q3", "bom.csv": b"a,b\n1,2\n"}))
    assert taken(parsed) == ["reports/q3/summary.md", "bom.csv"]
    assert [a.name for a in parsed.attachments] == ["summary.md", "bom.csv"]


def test_e4_7_blocks_point_at_their_line_in_doc_md() -> None:
    parsed = run("zip", make_zip({"a.md": b"a", "b.md": b"b", "x.exe": b"MZ"}))
    lines = str(parsed.markdown).splitlines()
    for block in parsed.blocks:
        assert block.line is not None and block.text in lines[block.line - 1]
    assert "- x.exe: UNSUPPORTED_TYPE" in lines


@pytest.mark.parametrize(("kind", "build"), KINDS)
def test_e4_7_directory_entries_are_not_members(kind: str, build: Callable) -> None:  # type: ignore[type-arg]
    parsed = run(
        kind,
        build({"docs/": b"", "docs/a.md": b"a"}) if kind == "zip" else build({"docs/a.md": b"a"}),
    )
    assert parsed.report.archive is not None and parsed.report.archive.members_total == 1


# --- limits and bombs (E4.7-T02) -----------------------------------------------------------------


def test_e4_7_t02_a_zip_bomb_is_refused_before_anything_is_read() -> None:
    bomb = make_zip({"zeros.md": b"\0" * (150 * MB)})
    assert len(bomb) < 2 * MB
    tracemalloc.start()
    started = time.monotonic()
    assert refused("zip", bomb) == ErrorCode.ARCHIVE_LIMIT
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert time.monotonic() - started < 10 and peak < 40 * MB  # nothing near 150 MB was unpacked


def test_e4_7_t02_a_ratio_over_100_is_refused() -> None:
    squeezed = make_zip({"a.md": b"\0" * (30 * MB)})  # 30 MB in a few KB: ratio in the thousands
    assert refused("zip", squeezed) == ErrorCode.ARCHIVE_LIMIT


def test_e4_7_t02_members_that_add_up_to_over_100_mb_are_refused() -> None:
    parts = {f"p{n}.csv": os.urandom(11 * MB) for n in range(10)}  # 110 MB, ratio about 1
    assert refused("zip", make_zip(parts, zipfile.ZIP_STORED)) == ErrorCode.ARCHIVE_LIMIT


def test_e4_7_t02_a_tar_gz_bomb_stops_while_it_is_read() -> None:
    def add(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("zeros.md")
        info.size = 200 * MB
        archive.addfile(info, io.BytesIO(b"\0" * (200 * MB)))

    bomb = make_tar(add)
    assert len(bomb) < 2 * MB
    tracemalloc.start()
    started = time.monotonic()
    assert refused("tar.gz", bomb) == ErrorCode.ARCHIVE_LIMIT
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert time.monotonic() - started < 15 and peak < 60 * MB


def test_e4_7_a_member_whose_header_lies_about_its_size_is_stopped_by_the_byte_count() -> None:
    data = bytearray(make_zip({"a.csv": b"\0" * (20 * MB)}, zipfile.ZIP_STORED))
    # the local header and the directory both say 1 KB; the stored data is really 20 MB
    for marker in (b"PK\x03\x04", b"PK\x01\x02"):
        at = bytes(data).find(marker)
        offset = at + (22 if marker == b"PK\x03\x04" else 24)
        data[offset : offset + 4] = (1024).to_bytes(4, "little")
    code = refused("zip", bytes(data))
    assert code in (ErrorCode.ARCHIVE_LIMIT, ErrorCode.CORRUPT)


def test_e4_7_a_member_over_15_mb_is_refused_and_the_others_are_taken() -> None:
    parsed = run(
        "zip", make_zip({"big.csv": os.urandom(16 * MB), "ok.md": b"# ok"}, zipfile.ZIP_STORED)
    )
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [("big.csv", ErrorCode.ARCHIVE_LIMIT)]


@pytest.mark.parametrize(("kind", "build"), KINDS)
def test_e4_7_only_20_members_are_taken_and_the_rest_are_listed(kind: str, build: Callable) -> None:  # type: ignore[type-arg]
    parsed = run(kind, build({f"f{n:02}.md": f"file {n}".encode() for n in range(25)}))
    assert len(parsed.attachments) == MAX_ARCHIVE_MEMBERS
    assert parsed.report.archive is not None and parsed.report.archive.members_total == 25
    assert rejected(parsed) == [(f"f{n:02}.md", ErrorCode.ARCHIVE_LIMIT) for n in range(20, 25)]


def test_e4_7_an_archive_with_too_many_entries_is_refused() -> None:
    many = {f"d{n}/": b"" for n in range(10_001)}
    assert refused("zip", make_zip(many)) == ErrorCode.ARCHIVE_LIMIT


# --- unsafe paths (E4.7-T03, T04) ---------------------------------------------------------------


UNSAFE = [
    "../../x.md",
    "a/../../x.md",
    "/etc/x.md",
    "\\windows\\x.md",
    "C:\\x.md",
    "c:/x.md",
    "..\\..\\x.md",
    "a\nb.md",
    "x" * 1100 + ".md",
]


@pytest.mark.parametrize("name", UNSAFE)
def test_e4_7_t03_an_unsafe_zip_member_is_refused_and_the_others_are_taken(name: str) -> None:
    parsed = run("zip", make_zip({name: b"evil", "ok.md": b"# ok"}))
    assert taken(parsed) == ["ok.md"]
    [(path, code)] = rejected(parsed)
    assert code == ErrorCode.PATH_UNSAFE and len(path) <= 1024 and "\x00" not in path


@pytest.mark.parametrize("name", UNSAFE[:7])
def test_e4_7_t03_an_unsafe_tar_member_is_refused_and_the_others_are_taken(name: str) -> None:
    parsed = run("tar.gz", targz({name: b"evil", "ok.md": b"# ok"}))
    assert taken(parsed) == ["ok.md"]
    assert [code for _, code in rejected(parsed)] == [ErrorCode.PATH_UNSAFE]


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_e4_7_t03_a_nul_in_a_member_name_never_gets_a_member_taken_under_that_name(
    kind: str,
) -> None:
    members = {"a\x00b.md": b"evil", "ok.md": b"# ok"}
    parsed = run(kind, make_zip(members) if kind == "zip" else targz(members))
    assert taken(parsed) == ["ok.md"]  # the reader cuts the name at the NUL: "a", with no type
    codes = [code for _, code in rejected(parsed)]
    assert codes in ([ErrorCode.UNSUPPORTED_TYPE], [ErrorCode.PATH_UNSAFE])


def test_e4_7_t03_nothing_is_written_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run("zip", make_zip({"../../x.md": b"evil", "ok.md": b"# ok"}))
    run("tar.gz", targz({"../../x.md": b"evil", "ok.md": b"# ok"}))
    assert list(tmp_path.iterdir()) == [] and not (tmp_path.parent / "x.md").exists()


def test_e4_7_t04_links_devices_and_fifos_in_a_tar_are_refused() -> None:
    def add(archive: tarfile.TarFile) -> None:
        tar_file(archive, "ok.md", b"# ok")
        for name, kind, target in (
            ("link.md", tarfile.SYMTYPE, "/etc/passwd"),
            ("hard.md", tarfile.LNKTYPE, "ok.md"),
            ("dev.md", tarfile.CHRTYPE, ""),
            ("blk.md", tarfile.BLKTYPE, ""),
            ("pipe.md", tarfile.FIFOTYPE, ""),
        ):
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, target
            archive.addfile(info)

    parsed = run("tar.gz", make_tar(add))
    assert taken(parsed) == ["ok.md"]
    assert rejected(parsed) == [
        (name, ErrorCode.PATH_UNSAFE)
        for name in ("link.md", "hard.md", "dev.md", "blk.md", "pipe.md")
    ]


def test_e4_7_t04_a_symlink_in_a_zip_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        link = zipfile.ZipInfo("link.md")
        link.external_attr = (0o120777 << 16) | 0x10  # a unix symlink
        archive.writestr(link, "/etc/passwd")
        archive.writestr("ok.md", "# ok")
    parsed = run("zip", out.getvalue())
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [("link.md", ErrorCode.PATH_UNSAFE)]


# --- nested archives, encryption, types (E4.7-T07) -----------------------------------------------


@pytest.mark.parametrize(
    "name", ["inner.zip", "inner.tar.gz", "inner.tgz", "inner.gz", "INNER.ZIP", "a.7z"]
)
def test_e4_7_t07_an_archive_inside_an_archive_is_refused(name: str) -> None:
    parsed = run("zip", make_zip({name: make_zip({"deep.md": b"deep"}), "ok.md": b"# ok"}))
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [(name, ErrorCode.NESTED_ARCHIVE)]


def test_e4_7_t07_an_archive_under_another_name_is_found_by_its_content() -> None:
    disguised = make_zip({"notes.md": make_zip({"deep.md": b"deep"}), "ok.md": b"# ok"})
    parsed = run("zip", disguised)
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [
        ("notes.md", ErrorCode.NESTED_ARCHIVE)
    ]


def test_e4_7_a_nested_archive_is_never_opened() -> None:
    parsed = run("zip", make_zip({"inner.zip": b"not even a zip", "ok.md": b"# ok"}))
    assert rejected(parsed) == [("inner.zip", ErrorCode.NESTED_ARCHIVE)]


def test_e4_7_an_encrypted_zip_member_is_refused_and_the_others_are_taken() -> None:
    parsed = run(
        "zip", mark_encrypted(make_zip({"secret.pdf": b"%PDF-1.4", "ok.md": b"# ok"}), "secret.pdf")
    )
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [("secret.pdf", ErrorCode.ENCRYPTED)]


@pytest.mark.parametrize("name", ["setup.exe", "noextension", "run.sh", "x.dll"])
def test_e4_7_a_member_of_a_type_that_is_not_allowed_is_refused(name: str) -> None:
    parsed = run("zip", make_zip({name: b"data", "ok.md": b"# ok"}))
    assert taken(parsed) == ["ok.md"] and rejected(parsed) == [(name, ErrorCode.UNSUPPORTED_TYPE)]


# --- damaged and empty archives ---------------------------------------------------------------


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_e4_7_an_archive_with_no_member_left_is_empty(kind: str) -> None:
    only_bad = {"../x.md": b"a", "setup.exe": b"b"}
    data = make_zip(only_bad) if kind == "zip" else targz(only_bad)
    assert refused(kind, data) == ErrorCode.EMPTY


def test_e4_7_an_archive_with_no_entry_at_all_is_empty() -> None:
    assert refused("zip", make_zip({})) == ErrorCode.EMPTY
    assert refused("tar.gz", targz({})) == ErrorCode.EMPTY


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("zip", b""),
        ("zip", b"PK\x03\x04 not really"),
        ("zip", b"just text"),
        ("tar.gz", b""),
        ("tar.gz", b"\x1f\x8b not gzip"),
        ("tar.gz", gzip.compress(b"a gzip file that is not a tar archive" * 100)),
    ],
)
def test_e4_7_a_file_that_is_not_a_readable_archive_is_corrupt(kind: str, data: bytes) -> None:
    assert refused(kind, data) == ErrorCode.CORRUPT


def test_e4_7_a_truncated_archive_never_raises_anything_but_parse_error() -> None:
    for kind, data in (
        ("zip", fixture_path("documents", "sample.zip").read_bytes()),
        ("tar.gz", fixture_path("documents", "sample.tar.gz").read_bytes()),
    ):
        for size in range(0, len(data), 7):
            try:
                run(kind, data[:size])
            except ParseError:
                pass


# --- names ----------------------------------------------------------------------------------


def test_e4_7_a_tar_member_name_that_is_not_utf8_is_kept_readable() -> None:
    def add(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("caf\udce9.md")  # b"caf\xe9.md" read with surrogateescape
        info.size = 4
        archive.addfile(info, io.BytesIO(b"# ok"))

    parsed = run("tar.gz", make_tar(add))
    assert len(parsed.attachments) == 1
    path = parsed.attachments[0].path
    assert path.encode("utf-8") and path.startswith("caf") and path.endswith(".md")


def test_e4_7_two_members_with_the_same_path_are_both_taken() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("a.md", "first")
        archive.writestr("a.md", "second")
    parsed = run("zip", out.getvalue())
    assert [a.data for a in parsed.attachments] == [b"first", b"second"]


def test_e4_7_reported_rejections_are_valid_contract_objects() -> None:
    parsed = run("zip", make_zip({"../x.md": b"a", "ok.md": b"b"}))
    assert parsed.report.archive is not None
    assert parsed.report.archive.rejected == [
        RejectedMember(path="../x.md", code=ErrorCode.PATH_UNSAFE)
    ]
