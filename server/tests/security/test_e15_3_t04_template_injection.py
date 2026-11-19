"""E15.3-T04 template injection: ISATVON text reaches Jinja2 only as data, never as source (#137).

Every payload of the versioned corpus `fixtures/security/template_injection.txt` goes into each
free-text field the released codegen templates print (skill_id, action_step, acceptance_check, a
V parameter value, an enum value) and is rendered with the real `render_skeleton`. Then:

* Jinja2 compiled only the template files: no compiled source contains the payload;
* nothing ran: os.system / os.popen / subprocess are tripwires during the render;
* the payload comes back verbatim from the generated Python (a literal or a docstring), so no
  expression in it was evaluated, and the generated modules have the same imports and calls as a
  benign render (what the AST gate inspects);
* the workflow UI's index.html (`render_index`, autoescaped) shows it as escaped text.

test_codegen_templates.py (E11.1-T03) covers Python-level break-outs of the same filters; this file
covers Jinja2 syntax, the spec's `cycler.__init__.__globals__` payload and the data-block variants.
"""

import ast
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jinja2
import pytest
from fixtures import fixture_path
from markupsafe import escape
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.render import environment
from sbam_workers.build.ui_bundle import clean, load_ui_template, render_index

CORPUS = fixture_path("security", "template_injection.txt")
EDGE_PARAMS = Path(__file__).resolve().parents[1] / "golden" / "edge_enum_optional" / "params.json"
SPEC_PAYLOAD = '{{ cycler.__init__.__globals__.os.popen("id").read() }}'
DATA_BLOCK_VARIANTS = ("</DATA>", "</Data >", "</ data>", "<data name=", "<image name=")


def load_corpus() -> list[tuple[int, str]]:
    out = []
    for number, line in enumerate(CORPUS.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() and not line.startswith("# "):
            out.append((number, line))
    return out


PAYLOADS = load_corpus()


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.1")


def params_with(text: str) -> dict[str, Any]:
    """The edge fixture's params with `text` in every free-text field the templates print."""
    params = json.loads(EDGE_PARAMS.read_text(encoding="utf-8"))
    assert isinstance(params, dict)
    params["skill_id"] = text
    params["action_step"] = text
    params["acceptance_check"] = text
    params["parameters"] = [{"name": "banner", "value": text}]
    params["inputs"][0]["enum"] = [text, "benign"] if text != "benign" else ["benign"]
    return params


class Spy:
    """Records every source string Jinja2 compiles; makes process spawning a test failure."""

    def __init__(self) -> None:
        self.compiled: list[str] = []
        self.spawned: list[str] = []


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Spy]:
    seen = Spy()
    original = jinja2.Environment.compile

    def compile_(self: jinja2.Environment, source: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(source, str):
            seen.compiled.append(source)
        return original(self, source, *args, **kwargs)

    def tripwire(name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            seen.spawned.append(name)
            raise AssertionError(f"{name} called during render")

        return call

    monkeypatch.setattr(jinja2.Environment, "compile", compile_)
    monkeypatch.setattr(os, "system", tripwire("os.system"))
    monkeypatch.setattr(os, "popen", tripwire("os.popen"))
    monkeypatch.setattr(subprocess, "Popen", tripwire("subprocess.Popen"))
    yield seen


def words(text: str) -> str:
    return " ".join(text.split())


def shape(source: str) -> tuple[list[str], list[str]]:
    """What the AST gate looks at: the imports and every call target, in order."""
    tree = ast.parse(source)
    imports = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    return imports, calls


def docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)]
    return [d for d in (ast.get_docstring(n) for n in nodes) if d]


def assigned(source: str, target: str) -> ast.expr:
    tree = ast.parse(source)
    return next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and n.value is not None
        and target in ast.unparse(n.targets[0] if isinstance(n, ast.Assign) else n.target)
    )


# --- the corpus -----------------------------------------------------------------------------


def test_e15_3_t04_corpus_is_versioned_and_has_at_least_50_payloads() -> None:
    header = CORPUS.read_text(encoding="utf-8").splitlines()[0]
    texts = [p for _, p in PAYLOADS]

    assert "version 1" in header
    assert len(texts) >= 50
    assert len(set(texts)) == len(texts), "duplicate payloads"
    assert all(0 < len(p) <= 120 for p in texts), "a payload must fit skill_id and the UI title"
    assert SPEC_PAYLOAD in texts
    assert sum("{{" in p for p in texts) >= 30 and sum("{%" in p for p in texts) >= 15
    for variant in DATA_BLOCK_VARIANTS:
        assert any(variant in p for p in texts), variant


def test_e15_3_t04_spy_detects_text_used_as_template_source(
    template: LoadedTemplate, spy: Spy
) -> None:
    """The checks below can fail: text handed to from_string is compiled and evaluated."""
    rendered = environment(template).from_string("{{ 7*7 }}").render()

    assert rendered == "49"
    assert "{{ 7*7 }}" in spy.compiled


# --- every payload, every field -------------------------------------------------------------


@pytest.mark.parametrize("payload", [pytest.param(p, id=f"line{n}") for n, p in PAYLOADS])
def test_e15_3_t04_template_injection(template: LoadedTemplate, spy: Spy, payload: str) -> None:
    baseline = render_skeleton(template, params_with("benign")).files
    spy.compiled.clear()

    files = render_skeleton(template, params_with(payload)).files

    # 1. the template source never comes from manifest text
    assert spy.compiled, "nothing compiled: the spy is not wired"
    assert not [s for s in spy.compiled if payload in s], "payload compiled as Jinja2 source"
    # 2. no command ran
    assert spy.spawned == []
    # 3. the generated code has the benign render's shape: no new import or call
    for name, source in files.items():
        assert shape(source) == shape(baseline[name]), f"{name}: imports or calls changed"
    # 4. the payload comes back verbatim: it was printed as data, never evaluated
    assert ast.literal_eval(assigned(files["server.py"], "SKILL_ID")) == payload
    assert ast.literal_eval(assigned(files["state_handoff.py"], "SKILL_ID")) == payload
    params = assigned(files["state.py"], "PARAMETERS")
    assert isinstance(params, ast.Dict)
    assert ast.literal_eval(params.values[0]) == payload
    assert any(payload in values for values in literal_values(files["models.py"]))
    for name, field in (("action_tool.py", "action step"), ("check_tool.py", "acceptance check")):
        docs = [words(d) for d in docstrings(files[name])]
        assert any(words(payload) in d for d in docs), f"{field} not rendered literally in {name}"
    for name, source in files.items():
        module_doc = ast.get_docstring(ast.parse(source))
        assert module_doc is not None and payload in module_doc, f"{name}: skill_id in docstring"


def literal_values(models_source: str) -> list[tuple[Any, ...]]:
    """The values of every `Literal[...]` annotation in models.py (the enums)."""
    found = []
    for node in ast.walk(ast.parse(models_source)):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Literal"
        ):
            value = ast.literal_eval(node.slice)
            found.append(value if isinstance(value, tuple) else (value,))
    assert found, "no Literal[...] in models.py"
    return found


@pytest.mark.parametrize("payload", [pytest.param(p, id=f"line{n}") for n, p in PAYLOADS])
def test_e15_3_t04_ui_title_is_escaped_text(spy: Spy, payload: str) -> None:
    ui = load_ui_template()
    spy.compiled.clear()

    html = render_index(ui, payload).decode()

    assert not [s for s in spy.compiled if payload in s], "title compiled as Jinja2 source"
    assert spy.spawned == []
    assert f"<title>{escape(clean(payload, 120))}</title>" in html
    if any(ch in payload for ch in "<>&\"'"):
        assert payload not in html, "markup reached the page unescaped"
