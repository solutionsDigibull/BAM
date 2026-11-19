"""The drift gate of a Workflow UI bundle (WG-20, #768; ADR 0033).

A bundle must follow the pattern of its template, and nothing else is accepted:

  UI_FILE_MISSING       a file the template requires is not there
  UI_FILE_EXTRA         a file the template and the generator do not produce
  UI_TEMPLATE_DRIFT     a template file was changed, or the bundle names another template
  UI_PANE_MISSING       `src/App.tsx` lacks one of the three panes
  UI_DEPENDENCY         a dependency outside the template's list, another version, an install hook
  UI_IMPORT             a source file imports a package that is not an allowed dependency
  UI_HOST_UNDECLARED    a URL, socket or script reaches a host that is not in `allowed_hosts`
  UI_SECRET             something that looks like a key, token or password
  UI_UNSAFE_API         `eval`, `new Function`, `innerHTML`, `document.write`
  UI_PROVIDER           the data provider is not the Workflow server's (or is mock data)

Pure: the gate reads the files it is given and returns findings in the shape of the other gates.
Findings name the rule, the file and the line, never the secret value.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sbam_api.manifests.ui_bundle import finding
from sbam_contracts.build_dashboard import GateName, Severity
from sbam_contracts.manifest import host_problem

from sbam_workers.build.ui_bundle import (
    BUNDLE_FILE,
    GENERATED_FILE,
    PACKAGE_FILE,
    UiTemplate,
)

_TEXT_SUFFIXES: Final = (".ts", ".tsx", ".js", ".mjs", ".json", ".css", ".html", ".yaml", ".md")
_URL: Final = re.compile(
    r"""(?:(?:https?|wss?|ftp)://|(?<![:\w/.])//(?=[a-z0-9-]+\.))([A-Za-z0-9._*-]+)""", re.I
)
_IMPORT: Final = re.compile(
    r"""(?:\bfrom\s+|\bimport\s+|\bimport\(\s*|\brequire\(\s*)["']([^"']+)["']"""
)
_DYNAMIC: Final = re.compile(r"""\b(?:import|require)\(\s*[^"'\s)]""")
_SECRET: Final = re.compile(
    r"""sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{10,}"""
    r"""|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."""
    r"""|(?:api[_-]?key|secret|token|passw(?:or)?d)["']?\s*[:=]\s*["'][^"'\s]{8,}["']""",
    re.I,
)
_UNSAFE: Final = (
    ("eval(", re.compile(r"\beval\s*\(")),
    ("new Function", re.compile(r"\bnew\s+Function\b")),
    ("innerHTML", re.compile(r"\binnerHTML\b|dangerouslySetInnerHTML")),
    ("document.write", re.compile(r"\bdocument\.write\b")),
)
_OTHER_NETWORK: Final = re.compile(
    r"\b(?:XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts)\b"
)
_HOOKS: Final = ("preinstall", "install", "postinstall", "prepare", "prepublish")
_SOURCE_DIRS: Final = ("src/",)


@dataclass(frozen=True)
class UiGateVerdict:
    passed: bool
    findings: list[dict[str, Any]]


def _text(files: Mapping[str, bytes], path: str) -> str | None:
    if path not in files or not path.endswith(_TEXT_SUFFIXES):
        return None
    return files[path].decode("utf-8", "replace")


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _allowed(host: str, allowed: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    for rule in allowed:
        if rule.startswith("*."):
            if host.endswith(rule[1:]) and host != rule[2:]:
                return True
        elif host == rule:
            return True
    return False


def _package_of(specifier: str) -> str:
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def _bad(code: str, file: str, line: int | None, message: str) -> dict[str, Any]:
    return finding(GateName.UI_BUNDLE, code, file, line, Severity.HIGH, message)


def _structure(files: Mapping[str, bytes], tpl: UiTemplate) -> list[dict[str, Any]]:
    found = [
        _bad("UI_FILE_MISSING", p, None, f"required file {p} is missing")
        for p in tpl.required_files
        if p not in files
    ]
    known = set(tpl.static) | {tpl.entry_file, PACKAGE_FILE, BUNDLE_FILE, GENERATED_FILE}
    found += [
        _bad("UI_FILE_EXTRA", p, None, f"{p} is not part of the pattern")
        for p in sorted(files)
        if p not in known
    ]
    for path, data in tpl.static.items():
        if path in files and files[path] != data:
            found.append(
                _bad("UI_TEMPLATE_DRIFT", path, None, f"{path} differs from template {tpl.version}")
            )
    info = _text(files, BUNDLE_FILE)
    try:
        ref = (json.loads(info or "")["template"]) if info else {}
    except (ValueError, KeyError, TypeError):
        ref = {}
    if ref.get("sha256") != tpl.sha256 or ref.get("version") != tpl.version:
        found.append(
            _bad("UI_TEMPLATE_DRIFT", BUNDLE_FILE, None, "the bundle does not name this template")
        )
    app = _text(files, "src/App.tsx") or ""
    found += [
        _bad("UI_PANE_MISSING", "src/App.tsx", None, f"the {pane} pane is missing")
        for pane in tpl.required_panes
        if f'data-pane="{pane}"' not in app
    ]
    return found


def _dependencies(files: Mapping[str, bytes], tpl: UiTemplate) -> list[dict[str, Any]]:
    raw = _text(files, PACKAGE_FILE)
    if raw is None:
        return []
    try:
        package = json.loads(raw)
    except ValueError:
        return [_bad("UI_DEPENDENCY", PACKAGE_FILE, None, "package.json is not valid JSON")]
    found: list[dict[str, Any]] = []
    for section, allowed in (
        ("dependencies", tpl.dependencies),
        ("devDependencies", tpl.dev_dependencies),
    ):
        for name, version in (package.get(section) or {}).items():
            if name not in allowed:
                found.append(
                    _bad("UI_DEPENDENCY", PACKAGE_FILE, None, f"{name} is not an allowed library")
                )
            elif allowed[name] != version:
                found.append(
                    _bad("UI_DEPENDENCY", PACKAGE_FILE, None, f"{name} must be {allowed[name]}")
                )
    for hook in _HOOKS:
        if hook in (package.get("scripts") or {}):
            found.append(
                _bad("UI_DEPENDENCY", PACKAGE_FILE, None, f"the {hook} script is not allowed")
            )
    return found


def _sources(
    files: Mapping[str, bytes], tpl: UiTemplate, allowed_hosts: Sequence[str]
) -> list[dict[str, Any]]:
    packages = set(tpl.dependencies) | set(tpl.dev_dependencies) | {"react/jsx-runtime"}
    found: list[dict[str, Any]] = []
    for path in sorted(files):
        text = _text(files, path)
        if text is None or path == BUNDLE_FILE:
            continue
        in_source = path.startswith(_SOURCE_DIRS) and path.endswith((".ts", ".tsx"))
        if in_source:
            for match in _IMPORT.finditer(text):
                spec = match.group(1)
                if not spec.startswith(".") and _package_of(spec) not in packages:
                    found.append(
                        _bad(
                            "UI_IMPORT",
                            path,
                            _line(text, match.start()),
                            f"{spec} is not an allowed library",
                        )
                    )
            for match in _DYNAMIC.finditer(text):
                found.append(
                    _bad(
                        "UI_IMPORT",
                        path,
                        _line(text, match.start()),
                        "a dynamic import must name a literal module",
                    )
                )
            for label, pattern in _UNSAFE:
                for match in pattern.finditer(text):
                    found.append(
                        _bad(
                            "UI_UNSAFE_API",
                            path,
                            _line(text, match.start()),
                            f"{label} is not allowed",
                        )
                    )
            for match in _OTHER_NETWORK.finditer(text):
                found.append(
                    _bad(
                        "UI_HOST_UNDECLARED",
                        path,
                        _line(text, match.start()),
                        f"{match.group(0)} is not allowed: use the Workflow server provider",
                    )
                )
        for match in _URL.finditer(text):
            host = match.group(1)
            if not _allowed(host, allowed_hosts):
                found.append(
                    _bad(
                        "UI_HOST_UNDECLARED",
                        path,
                        _line(text, match.start()),
                        f"host {host} is not in allowed_hosts",
                    )
                )
        for match in _SECRET.finditer(text):
            found.append(_bad("UI_SECRET", path, _line(text, match.start()), "looks like a secret"))
    return found


def _provider(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    text = _text(files, "src/provider.ts") or ""
    problems = []
    if "callTool" not in text or "WORKFLOW.serverName" not in text:
        problems.append("the provider does not call the Workflow server's tools")
    if re.search(r"mock", text, re.I):
        problems.append("the provider serves mock data")
    return [_bad("UI_PROVIDER", "src/provider.ts", None, p) for p in problems]


def check_ui_bundle_files(
    files: Mapping[str, bytes], template: UiTemplate, allowed_hosts: Sequence[str] = ()
) -> UiGateVerdict:
    """Every drift rule over the bundle's files; `allowed_hosts` are the manifest entry's."""
    found: list[dict[str, Any]] = []
    for host in allowed_hosts:
        if (problem := host_problem(host)) is not None:
            found.append(_bad("UI_HOST_INVALID", host, None, f"host {host!r} {problem}"))
    found += _structure(files, template)
    found += _dependencies(files, template)
    found += _sources(files, template, allowed_hosts)
    found += _provider(files)
    return UiGateVerdict(not found, found)
