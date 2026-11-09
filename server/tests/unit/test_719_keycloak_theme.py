"""#719: the BuLLM login theme is mounted into Keycloak and keeps every page section Keycloak
fills in; the local seed switches the realm to it, and to one form for email and password."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "compose"
THEME = COMPOSE / "keycloak" / "themes" / "bullm" / "login"
SEED = COMPOSE / "keycloak" / "seed.sh"


class ComposeLoader(yaml.SafeLoader):
    """Compose's own tags read as the value they wrap."""


ComposeLoader.add_constructor("!reset", lambda loader, node: None)
ComposeLoader.add_constructor("!override", lambda loader, node: None)


def test_719_t01_the_theme_folder_is_mounted_read_only_into_keycloak() -> None:
    text = (COMPOSE / "compose.yaml").read_text(encoding="utf-8")
    document = yaml.load(text, Loader=ComposeLoader)  # noqa: S506 - SafeLoader plus Compose's tags
    volumes = document["services"]["keycloak"]["volumes"]
    assert "./keycloak/themes/bullm:/opt/keycloak/themes/bullm:ro" in volumes


def test_719_t02_the_theme_extends_keycloak_v2_so_every_page_keeps_working() -> None:
    props = (THEME / "theme.properties").read_text(encoding="utf-8")
    assert re.search(r"^parent=keycloak\.v2$", props, re.MULTILINE)
    assert "css/bullm.css" in props


def test_719_t03_the_page_shell_keeps_every_section_keycloak_fills_in() -> None:
    template = (THEME / "template.ftl").read_text(encoding="utf-8")
    for section in ("header", "form", "info", "socialProviders", "show-username"):
        assert f'<#nested "{section}">' in template, section
    assert "<#macro registrationLayout" in template
    # the messages, the language picker and the try-another-way link are Keycloak's, not ours
    assert "message.summary" in template and "locale.supported" in template
    assert "showTryAnotherWayLink" in template


def test_719_t04_the_card_carries_the_artwork_and_the_wordmark_that_ship_with_the_theme() -> None:
    template = (THEME / "template.ftl").read_text(encoding="utf-8")
    for image in ("bull-panel.png", "bullm-wordmark-dark.png", "bullm-wordmark-light.png"):
        assert f"img/{image}" in template, image
        assert (THEME / "resources" / "img" / image).is_file(), image
    assert (THEME / "resources" / "css" / "bullm.css").is_file()


def test_719_t05_the_panel_claims_only_what_sbam_does_today() -> None:
    template = (THEME / "template.ftl").read_text(encoding="utf-8").lower()
    assert "staging" not in template and "deploy" not in template


def test_719_t06_the_welcome_title_replaces_the_default_one() -> None:
    messages = (THEME / "messages" / "messages_en.properties").read_text(encoding="utf-8")
    assert "loginAccountTitle=Welcome back" in messages


def test_719_t07_the_seed_sets_the_theme_and_turns_off_only_the_identity_first_step() -> None:
    seed = SEED.read_text(encoding="utf-8")
    assert "loginTheme=bullm" in seed
    assert "authentication/flows/browser/executions" in seed
    assert '\\"requirement\\":\\"DISABLED' in seed
    assert ",Organization,0$" in seed  # the one step; tenants stay Keycloak Organizations
    assert b"\r\n" not in SEED.read_bytes(), "the seed runs under bash in a container: LF only"


@pytest.mark.skipif(sys.platform == "win32", reason="no bash on a Windows host; Linux CI runs it")
def test_719_t08_the_seed_script_is_valid_bash() -> None:
    # fixed argv, our own script, no shell: `bash -n` only parses it
    result = subprocess.run(  # noqa: S603
        ["bash", "-n", str(SEED)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_719_t09_the_realm_file_is_not_changed_by_the_theme() -> None:
    # the realm is imported once and many tests pin its content; the theme reaches a realm through
    # the seed locally and through the realm settings in staging and production
    realm = (COMPOSE / "keycloak" / "realm-bullm.json").read_text(encoding="utf-8")
    assert "loginTheme" not in realm
