"""E4.6 loaders for JSON, XML, YAML (OpenAPI, FastMCP) and source code: key paths, symbols."""

import time
import uuid
from pathlib import Path

import pytest
from fixtures import fixture_path
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, ParsedDocument
from sbam_workers.ingest.loaders import LOADERS, Parsed, ParseError, loader_for, no_heartbeat


def docs(name: str) -> bytes:
    return fixture_path("documents", name).read_bytes()


def run(kind: str, data: bytes) -> Parsed:
    loader = loader_for(kind)
    assert loader is not None
    return loader(data, no_heartbeat)


def refused(kind: str, data: bytes) -> ErrorCode:
    with pytest.raises(ParseError) as caught:
        run(kind, data)
    return caught.value.code


def blocks_of(parsed: Parsed) -> list[Block]:
    return list(parsed.blocks)


def markdown_of(parsed: Parsed) -> str:
    assert isinstance(parsed.markdown, str)
    return parsed.markdown


def paths(parsed: Parsed) -> dict[str, str]:
    """The key paths of a structured file: `path: value` blocks, as a dict."""
    found = {}
    for block in blocks_of(parsed):
        path, _, value = block.text.partition(": ")
        found[path] = value
    return found


def test_e4_6_every_structured_and_code_type_has_a_loader() -> None:
    for kind in ("json", "xml", "yaml", "yml", "py", "sql", "js", "go", "cpp", "eml", "msg"):
        assert kind in LOADERS


# --- JSON --------------------------------------------------------------------------------------


def test_e4_6_json_gives_a_key_path_for_every_value_and_the_lines_match_doc_md() -> None:
    parsed = run("json", docs("sample.json"))
    found = paths(parsed)
    assert found["rule"] == "RULE-BOM" and found["scrap_rate"] == "0.015"
    assert found["approved_suppliers[1]"] == "Mouser"
    lines = markdown_of(parsed).splitlines()
    for block in blocks_of(parsed):
        assert lines[(block.line or 0) - 1] == block.text  # the key path can be found again
    ParsedDocument(document_version_id=uuid.uuid4(), loader="structured", blocks=blocks_of(parsed))


def test_e4_6_fastmcp_spec_tool_names_are_key_paths() -> None:
    found = paths(run("json", docs("fastmcp_spec.json")))
    assert found["tools[0].name"] == "scrub_bom"
    assert found["tools[0].input_schema.required[0]"] == "bom_ref"


@pytest.mark.parametrize("text", ["{", "{'a': 1}", '{"a": NaN}', '{"a": Infinity}', "\x00{}"])
def test_e4_6_json_that_is_not_strict_json_is_corrupt(text: str) -> None:
    assert refused("json", text.encode()) == ErrorCode.CORRUPT


def test_e4_6_json_nested_over_the_depth_limit_is_refused() -> None:
    assert refused("json", b"[" * 150 + b"]" * 150) == ErrorCode.PARSE_ERROR


@pytest.mark.parametrize("text", ["{}", "[]", "null", "   "])
def test_e4_6_json_with_no_values_is_empty(text: str) -> None:
    # blank space is not JSON at all
    assert refused("json", text.encode()) in (ErrorCode.EMPTY, ErrorCode.CORRUPT)


# --- XML (E4.6-T04) ----------------------------------------------------------------------------


def test_e4_6_xml_attributes_and_repeated_elements_get_key_paths() -> None:
    found = paths(run("xml", docs("sample.xml")))
    assert found["bom.@project"] == "NAV-9"
    assert found["bom.line[0].@cpn"] == "C-100"
    assert found["bom.line[4].@qty"] == "6"


def test_e4_6_t04_xxe_external_entity_is_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    xml = f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "{secret.as_uri()}">]>' "<d>&x;</d>"
    with pytest.raises(ParseError) as caught:
        run("xml", xml.encode())
    assert caught.value.code == ErrorCode.CORRUPT  # a DTD is refused: PARSE_FAILED, nothing read
    assert "TOP-SECRET" not in str(caught.value)


def test_e4_6_t04_a_billion_laughs_xml_is_refused_quickly() -> None:
    lols = "".join(f'<!ENTITY l{n} "&l{n - 1};&l{n - 1};&l{n - 1};">' for n in range(1, 9))
    xml = f'<!DOCTYPE b [<!ENTITY l0 "lol">{lols}]><b>&l8;</b>'
    started = time.monotonic()
    assert refused("xml", xml.encode()) == ErrorCode.CORRUPT
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("text", ["<a><b></a>", "not xml", ""])
def test_e4_6_broken_xml_is_corrupt(text: str) -> None:
    assert refused("xml", text.encode()) == ErrorCode.CORRUPT


def test_e4_6_xml_nested_over_the_depth_limit_is_refused() -> None:
    assert refused("xml", b"<a>" * 150 + b"</a>" * 150) == ErrorCode.PARSE_ERROR


# --- YAML and OpenAPI ----------------------------------------------------------------------------


def test_e4_6_t06_openapi_gives_key_paths_for_each_operation() -> None:
    found = paths(run("yaml", docs("openapi.yaml")))
    assert found["paths./quotes.get.summary"] == "List quotes"
    assert found["paths./quotes.post.summary"] == "Create a quote"
    assert found["paths./quotes.post.responses.201.description"] == "Created"
    assert found["openapi"] == "3.0.3"


def test_e4_6_yaml_lists_and_scalars_get_key_paths() -> None:
    found = paths(run("yaml", docs("sample.yaml")))
    assert found["stop_conditions[1]"] == "missing FX rate" and found["validity_days"] == "14"


def test_e4_6_yml_is_read_like_yaml() -> None:
    assert paths(run("yml", b"a:\n  b: 1\n")) == {"a.b": "1"}


def test_e4_6_t05_a_yaml_alias_bomb_is_corrupt_within_the_limits() -> None:
    lines = ['a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]']
    previous = "a"
    for name in "bcdefghi":
        lines.append(f"{name}: &{name} [{', '.join(['*' + previous] * 9)}]")
        previous = name
    started = time.monotonic()
    assert refused("yaml", "\n".join(lines).encode()) == ErrorCode.CORRUPT
    assert time.monotonic() - started < 30


def test_e4_6_yaml_cannot_build_python_objects(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    path = f"!!python/object/apply:pathlib.Path [{marker.as_posix()!r}]"
    text = f"x: !!python/object/apply:pathlib.Path.touch [{path}]"
    assert refused("yaml", text.encode()) == ErrorCode.CORRUPT
    assert not marker.exists()


@pytest.mark.parametrize("text", ["a: [1, 2", "a: b: c", "- a\n---\n- b\n", "\t- x"])
def test_e4_6_broken_yaml_is_corrupt(text: str) -> None:
    assert refused("yaml", text.encode()) == ErrorCode.CORRUPT


def test_e4_6_a_multiline_value_stays_on_one_line() -> None:
    parsed = run("yaml", b"note: |\n  first\n  second\n")
    assert paths(parsed)["note"] == '"first\\nsecond\\n"'
    assert len(markdown_of(parsed).splitlines()) == 1


# --- source code (E4.6-T07) ----------------------------------------------------------------


def test_e4_6_t07_python_symbols_have_the_right_lines() -> None:
    source = docs("sample.py")
    parsed = run("py", source)
    lines = source.decode().splitlines()
    heads = {b.text: b.line for b in blocks_of(parsed) if b.type == "heading"}
    assert heads == {
        "function extended_cost": 4,
        "class Line": 9,
        "method Line.__init__": 10,
    }
    for text, line in heads.items():
        assert line is not None and lines[line - 1].lstrip().startswith(("def ", "class "))
        assert text.split()[-1].split(".")[-1] in lines[line - 1]
    assert "- function `extended_cost` (line 4)" in markdown_of(parsed)
    assert "```python" in markdown_of(parsed)


def test_e4_6_t07_uploaded_python_is_never_run(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    source = (
        f"import os\nopen({str(marker)!r}, 'w').write('x')\n"
        "raise SystemExit(1)\n\ndef f():\n    return 1\n"
    )
    parsed = run("py", source.encode())
    assert not marker.exists()
    assert [b.text for b in blocks_of(parsed) if b.type == "heading"] == ["function f"]


def test_e4_6_python_with_a_syntax_error_falls_back_to_text_with_a_warning() -> None:
    parsed = run("py", b"def broken(:\n    pass\n")
    assert [b for b in blocks_of(parsed) if b.type == "heading"] == []
    assert blocks_of(parsed) and "def broken" in markdown_of(parsed)
    assert any("not valid Python" in w for w in parsed.report.warnings)


def test_e4_6_python_nested_too_deeply_for_the_parser_falls_back_to_text() -> None:
    source = ("(" * 5_000 + "1" + ")" * 5_000 + "\n").encode()
    parsed = run("py", source)
    assert parsed.report.warnings  # the parser gave up: the text is still there


def test_e4_6_python_methods_nested_classes_and_async_functions_are_listed() -> None:
    source = b"class A:\n    class B:\n        def m(self): ...\n\nasync def go(): ...\n"
    heads = [b.text for b in blocks_of(run("py", source)) if b.type == "heading"]
    assert heads == ["class A", "class A.B", "method A.B.m", "function go"]


@pytest.mark.parametrize("kind", ["sql", "js", "go", "cpp"])
def test_e4_6_other_languages_are_read_as_text_in_a_fenced_block(kind: str) -> None:
    parsed = run(kind, docs(f"sample.{kind}"))
    assert blocks_of(parsed) and all(b.type == "paragraph" for b in blocks_of(parsed))
    assert markdown_of(parsed).startswith("```") and markdown_of(parsed).rstrip().endswith("```")


@pytest.mark.parametrize("kind", ["py", "sql", "js", "go", "cpp"])
def test_e4_6_empty_or_binary_code_is_refused(kind: str) -> None:
    assert refused(kind, b"  \n") == ErrorCode.EMPTY
    assert refused(kind, b"\xff\xfe\x00bad") == ErrorCode.CORRUPT


def test_e4_6_source_that_contains_a_code_fence_does_not_close_doc_md_early() -> None:
    parsed = run("py", b'DOC = """\n```\nnot a fence\n```\n"""\n')
    text = markdown_of(parsed)
    assert text.startswith("````python") and text.rstrip().endswith("````")
