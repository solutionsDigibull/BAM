"""E16.3 (#142): dev/staging/prod Compose overlays, reverse proxy, secrets and Netbird policies.

Everything here is static: `docker compose config` resolves the files (it starts nothing), the
Caddyfile and policies are parsed as text/JSON. Tests that need a live host (nmap, testssl, Netbird
peers, TLS issuance, the real boot order) are the manual checklist in docs/runbooks/environments.md
and are not simulated here.
"""

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "compose"
DOCKER = shutil.which("docker")
SH = shutil.which("sh") or "/bin/sh"
BASH = shutil.which("bash") or "/bin/bash"


def load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


lint = load("scripts/compose_lint.py", "compose_lint")
matrix = load("scripts/netbird_matrix.py", "netbird_matrix")
render_realm = load("compose/keycloak/render_realm.py", "render_realm")

ENVS = ("dev", "staging", "prod")
ALL_ADDR = "0.0.0.0"  # noqa: S104 - asserting on it, not binding
DEPLOY_FILES = (
    "compose.deploy.yaml",
    "compose.dev.yaml",
    "compose.staging.yaml",
    "compose.prod.yaml",
    "compose.deploy.obs.yaml",
    "compose.deploy.analytics.yaml",
)


def _docker_compose_ok() -> bool:
    if DOCKER is None:
        return False
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [DOCKER, "compose", "version"], capture_output=True, text=True, check=False
    )
    return proc.returncode == 0


needs_docker = pytest.mark.skipif(not _docker_compose_ok(), reason="docker compose not installed")


@pytest.fixture(scope="module")
def env_file() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield lint.synth_env_file(Path(tmp))


@pytest.fixture(scope="module")
def configs(env_file: Path) -> dict[str, dict[str, Any]]:
    return {env: lint.resolve(env, [], env_file) for env in ENVS}


@pytest.fixture(scope="module")
def full_prod(env_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = lint.resolve("prod", ["obs", "analytics"], env_file)
    return result


def errors(findings: list[Any]) -> list[str]:
    return [str(f) for f in findings if f.level == "error"]


# --- E16.3-T04: compose lint fails on :latest or an inline password -------------------------------


@needs_docker
@pytest.mark.parametrize("env", ENVS)
def test_e16_3_t04_real_overlays_pass_lint(configs: dict[str, dict[str, Any]], env: str) -> None:
    assert errors(lint.lint_config(configs[env], env)) == []
    assert [f for f in lint.lint_config(configs[env], env) if f.level == "warning"] == []


@needs_docker
def test_e16_3_t04_latest_tag_fails(configs: dict[str, dict[str, Any]]) -> None:
    cfg = copy.deepcopy(configs["prod"])
    cfg["services"]["sbam-web"]["image"] = "ghcr.io/solutionsdigibull/sbam-web:latest"
    found = errors(lint.lint_config(cfg, "prod"))
    assert any("R1 sbam-web" in f and ":latest" in f for f in found)
    cfg["services"]["sbam-web"]["image"] = "cgr.dev/x/minio:latest@sha256:" + "ab" * 32
    assert any("R1 sbam-web" in f and ":latest" in f for f in errors(lint.lint_config(cfg, "prod")))


@needs_docker
def test_e16_3_t04_tag_without_digest_and_build_fail(
    configs: dict[str, dict[str, Any]],
) -> None:
    cfg = copy.deepcopy(configs["staging"])
    cfg["services"]["postgres"]["image"] = "postgres:16.15-alpine"
    cfg["services"]["sbam-api"]["build"] = {"context": ".."}
    found = errors(lint.lint_config(cfg, "staging"))
    assert any("R1 postgres" in f and "not pinned by digest" in f for f in found)
    assert any("R1 sbam-api" in f and "builds on the host" in f for f in found)


@needs_docker
def test_e16_3_t04_inline_password_fails(configs: dict[str, dict[str, Any]]) -> None:
    cfg = copy.deepcopy(configs["prod"])
    cfg["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = "hunter2hunter2"  # noqa: S105
    cfg["services"]["sbam-api"]["environment"][
        "DATABASE_URL"
    ] = "postgresql://sbam:hunter2@postgres/sbam"
    cfg["services"]["temporal"]["environment"]["X_TOKEN"] = "change-me-token"  # noqa: S105
    found = errors(lint.lint_config(cfg, "prod"))
    assert any("R4 postgres" in f and "POSTGRES_PASSWORD" in f for f in found)
    assert any("R4 sbam-api" in f and "password in a URL" in f for f in found)
    assert any("R4 temporal" in f and "X_TOKEN" in f for f in found)


@needs_docker
def test_e16_3_t04_secret_definition_rules(configs: dict[str, dict[str, Any]]) -> None:
    cfg = copy.deepcopy(configs["prod"])
    cfg["secrets"]["auth_secret"] = {"environment": "AUTH_SECRET"}
    cfg["services"]["sbam-web"]["secrets"][0]["mode"] = "0644"
    cfg["services"]["sbam-web"]["environment"][
        "AUTH_SECRET_FILE"
    ] = "/run/secrets/not_granted"  # noqa: S105
    found = errors(lint.lint_config(cfg, "prod"))
    assert any("R8 auth_secret" in f and "`file:` secret" in f for f in found)
    assert any("R8 sbam-web" in f and "mode 0400" in f for f in found)
    assert any("R8 sbam-web" in f and "not granted" in f for f in found)


@needs_docker
def test_e16_3_t04_ports_networks_limits_rules(
    configs: dict[str, dict[str, Any]],
) -> None:
    cfg = copy.deepcopy(configs["prod"])
    cfg["services"]["postgres"]["ports"] = [
        {
            "host_ip": ALL_ADDR,
            "published": "5432",
            "target": 5432,
            "protocol": "tcp",
        }
    ]
    cfg["services"]["temporal-ui"]["ports"][0]["host_ip"] = ALL_ADDR
    cfg["services"]["sbam-web"]["networks"]["sbam_edge"] = None
    del cfg["services"]["worker-llm"]["mem_limit"]
    cfg["services"]["sbam-api"]["depends_on"]["keycloak"]["condition"] = "service_started"
    del cfg["services"]["sbam-api"]["depends_on"]["api-migrate"]
    found = errors(lint.lint_config(cfg, "prod"))
    assert any("R3 postgres" in f for f in found)
    assert any("R3 temporal-ui" in f and "all addresses" in f for f in found)
    assert any("R7 sbam-web" in f for f in found)
    assert any("R5 worker-llm" in f for f in found)
    assert any("R6 sbam-api" in f and "service_started" in f for f in found)
    assert any("R6 sbam-api" in f and "api-migrate" in f for f in found)


@needs_docker
def test_e16_3_t04_non_prod_proxy_must_not_be_public(
    configs: dict[str, dict[str, Any]],
) -> None:
    cfg = copy.deepcopy(configs["staging"])
    for port in cfg["services"]["proxy"]["ports"]:
        port["host_ip"] = ALL_ADDR
    assert any(
        "R3 proxy" in f and "must not be public" in f
        for f in errors(lint.lint_config(cfg, "staging"))
    )
    prod = copy.deepcopy(configs["prod"])
    prod["services"]["proxy"]["ports"].append(
        {"host_ip": ALL_ADDR, "published": "8443", "target": 8443}
    )
    assert any(
        "R3 proxy" in f and "only 80 and 443" in f for f in errors(lint.lint_config(prod, "prod"))
    )


@needs_docker
def test_e16_3_t04_env_flag_rules(configs: dict[str, dict[str, Any]]) -> None:
    cfg = copy.deepcopy(configs["prod"])
    cfg["services"]["sbam-api"]["environment"]["SBAM_ENV"] = "development"
    cfg["services"]["sbam-api"]["environment"]["LLM_PROVIDER"] = "mock"
    cfg["services"]["sbam-web"]["environment"]["SBAM_DEV_AUTH"] = "1"
    cfg["services"]["keycloak"]["environment"]["SEED_USER_PASSWORD"] = ""
    cfg["services"]["mailpit"] = {
        "image": "x@sha256:" + "ab" * 32,
        "networks": {"sbam_internal": None},
    }
    found = errors(lint.lint_config(cfg, "prod"))
    assert any("SBAM_ENV must be production" in f for f in found)
    assert any("LLM_PROVIDER must be bifrost" in f for f in found)
    assert any("SBAM_DEV_AUTH must be empty" in f for f in found)
    assert any("SEED_USER_PASSWORD" in f for f in found)
    assert any("dev-only service" in f for f in found)


@needs_docker
@pytest.mark.parametrize("env", ["prod", "staging"])
def test_a01_t04_worker_without_sbam_env_fails_lint(
    configs: dict[str, dict[str, Any]], env: str
) -> None:
    cfg = copy.deepcopy(configs[env])
    cfg["services"]["worker-certify"]["environment"].pop("SBAM_ENV", None)
    cfg["services"]["worker-build"]["environment"]["SBAM_ENV"] = "development"
    found = errors(lint.lint_config(cfg, env))
    assert any("worker-certify" in f and "SBAM_ENV must be" in f for f in found)
    assert any("worker-build" in f and "SBAM_ENV must be" in f for f in found)


@needs_docker
def test_e16_3_t04_cli_passes_on_repo_and_fails_on_placeholder_release(
    tmp_path: Path,
) -> None:
    assert lint.main([]) == 0
    template = COMPOSE / "release.env.example"
    assert lint.main(["--release", str(template)]) == 1  # zero digests are placeholders
    real = tmp_path / "release.txt"
    real.write_text(
        "".join(
            f"{key}=ghcr.io/solutionsdigibull/{name}@sha256:{'ab' * 32}\n"
            for key, name in (
                ("SBAM_API_IMAGE", "sbam-api"),
                ("SBAM_WORKER_IMAGE", "sbam-worker"),
                ("SBAM_WEB_IMAGE", "sbam-web"),
                ("SBAM_MOCK_API_IMAGE", "sbam-mock-api"),
            )
        ),
        encoding="utf-8",
    )
    assert lint.main(["--release", str(real)]) == 0
    real.write_text(
        real.read_text(encoding="utf-8").replace("sbam-web@sha256", "sbam-web:latest@sha256")
    )
    assert lint.main(["--release", str(real)]) == 1


@needs_docker
def test_e16_3_t04_obs_and_analytics_debt_is_warnings_only(
    full_prod: dict[str, Any], configs: dict[str, dict[str, Any]]
) -> None:
    findings = lint.lint_config(full_prod, "prod", set(configs["prod"]["services"]))
    assert errors(findings) == []
    warned = {f.service for f in findings if f.level == "warning"}
    assert {"minio", "langfuse-web", "otel-collector"} <= warned
    # OTLP ports are not published when deployed; every ops UI binds to the Netbird address.
    for name in (
        "otel-collector",
        "langfuse-web",
        "posthog",
        "temporal-ui",
        "keycloak",
    ):
        ports = full_prod["services"][name]["ports"]
        assert len(ports) == 1
        assert (
            ports[0]["host_ip"] == "127.0.0.1"
        )  # SBAM_OPS_BIND from .env.example (the safe default)


# --- E16.3-T03: digest parity ---------------------------------------------------------------------


@needs_docker
def test_e16_3_t03_digest_parity_dev_staging_prod(
    configs: dict[str, dict[str, Any]],
) -> None:
    manifests = {env: lint.manifest(cfg) for env, cfg in configs.items()}
    assert lint.compare_manifests(manifests) == []
    assert manifests["staging"] == manifests["prod"]
    # Dev differs only by the mock upstream it adds.
    assert set(manifests["dev"]) - set(manifests["prod"]) == {"sbam-llm-mock"}
    assert all(lint.DIGEST_RE.match(image) for m in manifests.values() for image in m.values())


@needs_docker
def test_e16_3_t03_a_different_digest_is_reported(
    configs: dict[str, dict[str, Any]],
) -> None:
    manifests = {env: lint.manifest(cfg) for env, cfg in configs.items()}
    manifests["prod"] = {
        **manifests["prod"],
        "sbam-api": "ghcr.io/x/sbam-api@sha256:" + "cd" * 32,
    }
    found = lint.compare_manifests(manifests)
    assert [f.service for f in found] == ["sbam-api"]


@needs_docker
def test_e16_3_env_overlays_change_only_limits_and_flags_not_images(
    configs: dict[str, dict[str, Any]],
) -> None:
    allowed_keys = {
        "cpus",
        "mem_limit",
        "ports",
    }  # ports: prod's proxy is the only public bind
    for service, staging in configs["staging"]["services"].items():
        prod = configs["prod"]["services"][service]
        differing = {k for k in set(staging) | set(prod) if staging.get(k) != prod.get(k)}
        differing.discard("environment")
        assert differing <= allowed_keys, f"{service} differs in {differing - allowed_keys}"
        env_a, env_b = lint._env_pairs(staging), lint._env_pairs(prod)
        assert {k for k in set(env_a) | set(env_b) if env_a.get(k) != env_b.get(k)} <= {"SBAM_ENV"}


# --- E16.3-T01 / T02 (static half): only the proxy is public, ops UIs are bound privately ---------


@needs_docker
def test_e16_3_t01_only_proxy_80_443_public_and_other_ports_private(
    configs: dict[str, dict[str, Any]],
) -> None:
    published = {
        (name, int(p["published"]), p["host_ip"])
        for name, svc in configs["prod"]["services"].items()
        for p in svc.get("ports") or []
    }
    public = {(n, port) for n, port, host in published if host in (ALL_ADDR, "", None)}
    assert public == {("proxy", 80), ("proxy", 443)}
    assert {n for n, _, _ in published} == {"proxy", "temporal-ui", "keycloak"}
    for name in (
        "postgres",
        "temporal",
        "sbam-api",
        "tusd",
        "bifrost",
        "sbam-web",
        "clamav",
    ):
        assert not configs["prod"]["services"][name].get("ports"), name


@needs_docker
def test_e16_3_t02_ops_uis_are_not_proxy_routes() -> None:
    caddy = (COMPOSE / "proxy" / "Caddyfile").read_text(encoding="utf-8")
    upstreams = set(re.findall(r"reverse_proxy\s+(\S+)", caddy))
    assert upstreams == {"tusd:1080", "sbam-web:3000", "keycloak:8080"}
    for forbidden in (
        "temporal",
        "grafana",
        "langfuse",
        "posthog",
        "postgres",
        "bifrost",
        "sbam-api",
        "clamav",
    ):
        assert forbidden not in " ".join(upstreams)


# --- E16.3-T05: startup order ---------------------------------------------------------------------


@needs_docker
@pytest.mark.parametrize("env", ENVS)
def test_e16_3_t05_api_migrate_before_api_and_dependencies_are_healthy(
    configs: dict[str, dict[str, Any]], env: str
) -> None:
    services = configs[env]["services"]
    graph = {n: set(s.get("depends_on") or {}) for n, s in services.items()}
    order: list[str] = []
    visiting: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"dependency cycle at {node}"
        if node in order:
            return
        visiting.add(node)
        for dep in sorted(graph[node]):
            visit(dep)
        visiting.discard(node)
        order.append(node)

    for node in sorted(graph):
        visit(node)
    assert order.index("api-migrate") < order.index("sbam-api")
    assert order.index("postgres") < order.index("api-migrate")
    assert order.index("sbam-api") < order.index("tusd") < order.index("proxy")
    assert order.index("sbam-web") < order.index("proxy")
    for name in (n for n in services if n.startswith("worker-")):
        assert order.index("api-migrate") < order.index(name)
    for name, svc in services.items():
        for dep, spec in (svc.get("depends_on") or {}).items():
            assert spec["condition"] in {
                "service_healthy",
                "service_completed_successfully",
            }, (name, dep)
            if spec["condition"] == "service_healthy":
                has_check = services[dep].get("healthcheck") or dep in lint.IMAGE_HEALTHCHECK
                assert has_check, f"{name} waits for {dep}, which has no healthcheck"


def test_e16_3_t05_image_healthchecks_are_real() -> None:
    for service, dockerfile in lint.IMAGE_HEALTHCHECK.items():
        text = (COMPOSE / "docker" / dockerfile).read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text, f"{service} relies on a HEALTHCHECK that {dockerfile} lacks"


# --- E16.3-T06 / T08: proxy config ----------------------------------------------------------------

CADDY = (COMPOSE / "proxy" / "Caddyfile").read_text(encoding="utf-8")


def _kc_admin_pattern() -> re.Pattern[str]:
    match = re.search(r"@kc_admin path_regexp (\S+)", CADDY)
    assert match, "the Keycloak admin block is missing from the Caddyfile"
    return re.compile(match.group(1).replace("(?i)", ""), re.IGNORECASE)


@pytest.mark.parametrize(
    "path",
    [
        "/admin/master/console",
        "/admin/master/console/",
        "/admin/master",
        "/ADMIN/Master/console",
        "//admin/master/console",
        "/admin/realms/bullm/users",
        "/admin/realms",
        "/realms/master/protocol/openid-connect/token",
    ],
)
def test_e16_3_t06_keycloak_admin_paths_are_blocked(path: str) -> None:
    assert _kc_admin_pattern().search(path)


@pytest.mark.parametrize(
    "path",
    [
        "/admin/masters",
        "/admin/tenants",
        "/admin/users",
        "/realms/bullm/protocol/openid-connect/auth",
        "/",
    ],
)
def test_e16_3_t06_portal_and_login_paths_are_not_blocked(path: str) -> None:
    assert not _kc_admin_pattern().search(path)


def test_e16_3_t06_admin_block_precedes_routes_and_auth_site_is_an_allowlist() -> None:
    hardening = CADDY.split("(hardening)")[1].split("(tls_policy)")[0]
    assert "handle @kc_admin" in hardening and "respond 404" in hardening
    auth = CADDY.split("{$SBAM_AUTH_HOST}")[1].split("http://:8081")[0]
    assert "import hardening" in auth
    matcher = re.search(r"@oidc path (.+)", auth)
    assert matcher
    allowed = matcher.group(1).split()
    assert all(p.startswith(("/realms/bullm/", "/resources/")) for p in allowed)
    assert not any("master" in p or p.startswith("/admin") for p in allowed)
    assert re.search(r"handle \{\s+respond 404\s+\}", auth)  # everything else is a 404
    portal = CADDY.split("{$SBAM_PORTAL_HOST}")[1].split("{$SBAM_AUTH_HOST}")[0]
    assert "import hardening" in portal
    assert "@dev_pages path /dev /dev/*" in portal
    assert "@files path /files /files/*" in portal and "handle @files {" in portal


_BLOCK_DIRECTIVE = re.compile(r"^\s*(handle|handle_path|route)\b(.*)$")
_ONE_MATCHER = re.compile(r"^(\s+(@[\w-]+|/\S*))?\s*\{$")


def test_e16_3_block_directives_take_at_most_one_matcher() -> None:
    """#448: `handle /files /files/* {` made Caddy refuse the whole file. handle / handle_path /
    route accept one matcher token (a path or a named @matcher); two paths need a named matcher.
    Caught here without Docker; the real `caddy validate` runs in CI (SBAM_VALIDATE_PROXY)."""
    bad = [
        f"Caddyfile:{n}: {line.strip()}"
        for n, line in enumerate(CADDY.splitlines(), start=1)
        if (m := _BLOCK_DIRECTIVE.match(line)) and not _ONE_MATCHER.match(m.group(2))
    ]
    assert bad == []


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        ("\thandle {", True),
        ("\thandle @files {", True),
        ("\thandle /files/* {", True),
        ("\thandle_path /api/* {", True),
        ("\thandle /files /files/* {", False),
        ("\troute @a @b {", False),
        ("\thandle @files", False),  # no block
    ],
)
def test_e16_3_the_matcher_guard_itself(line: str, ok: bool) -> None:
    m = _BLOCK_DIRECTIVE.match(line)
    assert m is not None
    assert bool(_ONE_MATCHER.match(m.group(2))) is ok


def test_e16_3_t08_tls_policy_and_hsts() -> None:
    tls = CADDY.split("(tls_policy)")[1].split("\n}\n")[0]
    assert "protocols tls1.2 tls1.3" in tls
    assert not re.search(r"tls1\.[01]\b", CADDY.replace("tls1.2", "").replace("tls1.3", ""))
    hsts = re.search(r'Strict-Transport-Security "max-age=(\d+)', CADDY)
    assert hsts and int(hsts.group(1)) >= 31536000
    # Nothing switches encryption or the HTTP->HTTPS redirect off; plain http is only the
    # internal healthcheck listener.
    assert "auto_https" not in CADDY and "tls off" not in CADDY and "insecure" not in CADDY
    assert re.findall(r"^http://\S+", CADDY, re.MULTILINE) == ["http://:8081"]
    assert "admin off" in CADDY
    # The site blocks import the policy: both public hostnames get it.
    assert CADDY.count("import tls_policy") == 2 and CADDY.count("import hardening") == 2


def test_e16_3_proxy_service_hardening_and_secure_cookie_origin(
    configs_or_skip: None = None,
) -> None:
    text = (COMPOSE / "compose.deploy.yaml").read_text(encoding="utf-8")
    proxy = text.split("  proxy:\n")[1].split("\nnetworks:")[0]
    for needle in (
        "read_only: true",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "NET_BIND_SERVICE",
    ):
        assert needle in proxy
    # Auth.js sets Secure cookies when AUTH_URL is https (and the proxy sets X-Forwarded-Proto).
    assert "AUTH_URL: https://" in text


@pytest.mark.skipif(
    os.environ.get("SBAM_VALIDATE_PROXY") != "1",
    reason="set SBAM_VALIDATE_PROXY=1 (needs docker pull of the caddy image)",
)
def test_e16_3_caddyfile_validates_with_caddy() -> None:  # pragma: no cover - needs the image
    assert DOCKER
    image = next(
        m
        for m in re.findall(
            r"image: (caddy\S+)",
            (COMPOSE / "compose.deploy.yaml").read_text(encoding="utf-8"),
        )
    )
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [
            DOCKER,
            "run",
            "--rm",
            *("-e", "SBAM_PORTAL_HOST=portal.example.test"),
            *("-e", "SBAM_AUTH_HOST=auth.example.test"),
            *("-e", "SBAM_ACME_EMAIL=ops@example.test", "-e", "SBAM_TLS="),
            *("-v", f"{COMPOSE / 'proxy' / 'Caddyfile'}:/etc/caddy/Caddyfile:ro"),
            image,
            *("caddy", "validate", "--config", "/etc/caddy/Caddyfile"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


# --- E16.3-T07: Netbird policies ------------------------------------------------------------------


def test_e16_3_t07_dev_laptop_cannot_reach_prod_but_reaches_dev_and_staging() -> None:
    policy = matrix.load()
    for service in policy["ops_services"] + ["ssh"]:
        assert not matrix.is_allowed(policy, "dev-laptops", "vm-prod", service), service
        assert matrix.is_allowed(policy, "dev-laptops", "vm-dev", service)
        assert matrix.is_allowed(policy, "dev-laptops", "vm-staging", service)
    assert matrix.violations(policy) == []


def test_e16_3_t07_groups_workers_to_sandbox_and_platform_only_prod_ops() -> None:
    policy = matrix.load()
    assert set(policy["groups"]) == {
        "dev-laptops",
        "platform",
        "vm-dev",
        "vm-staging",
        "vm-prod",
        "sandbox-host",
        "ci-deploy",
        "backup-host",
    }
    for vm in ("vm-dev", "vm-staging", "vm-prod"):
        assert matrix.is_allowed(policy, vm, "sandbox-host", "sandbox-api")
        assert not matrix.is_allowed(policy, "sandbox-host", vm, "ssh")
    for service in policy["ops_services"] + ["ssh"]:
        sources = {
            s for s, d, svc in matrix.allowed_pairs(policy) if d == "vm-prod" and svc == service
        }
        # deploy jobs (E16.4) SSH to prod's deploy user; no ops UI
        assert sources == ({"platform", "ci-deploy"} if service == "ssh" else {"platform"}), service
    assert not any(rule.get("bidirectional") for rule in policy["policies"])


def test_e16_3_t07_published_ports_match_the_policy_services() -> None:
    policy = matrix.load()
    env_example = (COMPOSE / ".env.example").read_text(encoding="utf-8")
    ports = dict(re.findall(r"^([A-Z_]+_PORT)=(\d+)$", env_example, re.MULTILINE))
    expected = {
        "temporal-ui": "TEMPORAL_UI_PORT",
        "keycloak-admin": "KEYCLOAK_PORT",
        "grafana": "GRAFANA_PORT",
        "langfuse": "LANGFUSE_PORT",
        "posthog": "POSTHOG_PORT",
    }
    for service, var in expected.items():
        assert policy["services"][service]["port"] == int(ports[var]), service


def test_e16_3_t07_a_laptop_to_prod_policy_is_caught() -> None:
    policy = copy.deepcopy(matrix.load())
    policy["policies"].append(
        {
            "name": "oops",
            "sources": ["dev-laptops"],
            "destinations": ["vm-prod"],
            "services": ["ssh", "temporal-ui"],
        }
    )
    found = matrix.violations(policy)
    assert any("dev-laptops -> vm-prod ssh is allowed" in f for f in found)
    assert any("production ops is for the platform group only" in f for f in found)
    policy["policies"][-1]["sources"] = ["nobody"]
    with pytest.raises(ValueError, match="unknown group nobody"):
        matrix.allowed_pairs(policy)


# --- secrets: files on the VM, Docker secrets, never inline ---------------------------------------

SECRET_NAMES = [
    "postgres_password",
    "sbam_db_password",
    "temporal_db_password",
    "keycloak_db_password",
    "langfuse_db_password",
    "keycloak_admin_password",
    "keycloak_admin_client_secret",
    "tusd_hook_secret",
    "storage_signing_key",
    "auth_secret",
    "openrouter_api_key",
    "cognee_user_secret",
    "bifrost_virtual_key",
]


def _write_secrets(directory: Path, **overrides: str | None) -> None:
    for name in SECRET_NAMES:
        value = overrides.get(
            name,
            (
                "a1b2c3d4e5f60718293a4b5c6d7e8f90"
                if "password" in name
                else "s3cret-value-0123456789abcdef0123456789"
            ),
        )
        if value is None:
            continue
        path = directory / name
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o400)


def _check_secrets(
    directory: Path, *args: str, env_name: str = "prod"
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SBAM_SECRETS_OWNER_UID": str(os.getuid())}
    cmd = [BASH, str(REPO / "scripts" / "check-secrets.sh"), "--env", env_name]
    return subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
        [*cmd, "--dir", str(directory), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_e16_3_secrets_complete_and_0400_passes(tmp_path: Path) -> None:
    _write_secrets(tmp_path)
    out = _check_secrets(tmp_path)
    assert out.returncode == 0, out.stdout
    assert "OK" in out.stdout and "a1b2c3d4" not in out.stdout  # never prints a value


def test_e16_3_secrets_names_match_compose_secrets_and_script() -> None:
    deploy = (COMPOSE / "compose.deploy.yaml").read_text(encoding="utf-8")
    declared = re.findall(
        r'^  (\w+): \{ file: "\$\{SBAM_SECRETS_DIR:-/etc/sbam/secrets\}/(\w+)" \}$',
        deploy,
        re.MULTILINE,
    )
    assert [a for a, _ in declared] == SECRET_NAMES and all(a == b for a, b in declared)
    script = (REPO / "scripts" / "check-secrets.sh").read_text(encoding="utf-8")
    listed = re.search(r'DB_SECRETS="([^"]+)"', script), re.search(
        r'OTHER_SECRETS="([^"]+)"', script
    )
    assert listed[0] and listed[1]
    assert (listed[0].group(1) + " " + listed[1].group(1)).split() == SECRET_NAMES


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o444, 0o440])
def test_e16_3_secrets_wrong_mode_fails(tmp_path: Path, mode: int) -> None:
    _write_secrets(tmp_path)
    (tmp_path / "auth_secret").chmod(mode)
    out = _check_secrets(tmp_path)
    assert out.returncode == 1 and "auth_secret: mode is" in out.stdout


def test_e16_3_secrets_missing_empty_placeholder_and_unsafe_fail(
    tmp_path: Path,
) -> None:
    _write_secrets(
        tmp_path,
        tusd_hook_secret=None,
        storage_signing_key="",
        auth_secret="change-me-auth",  # noqa: S106
        sbam_db_password="has/slash+and@signs-0123456789",  # noqa: S106
        openrouter_api_key="",
    )
    out = _check_secrets(tmp_path)
    assert out.returncode == 1
    for needle in (
        "tusd_hook_secret: missing",
        "storage_signing_key: empty",
        "auth_secret: still a template placeholder",
        "sbam_db_password: must be 16+ URL-safe",
        "openrouter_api_key: empty",
    ):
        assert needle in out.stdout, needle


def test_e16_3_secrets_dev_may_have_an_empty_openrouter_key(tmp_path: Path) -> None:
    _write_secrets(tmp_path, openrouter_api_key="")
    assert _check_secrets(tmp_path, env_name="dev").returncode == 0
    assert _check_secrets(tmp_path, env_name="prod").returncode == 1


def test_e16_3_secrets_symlink_is_rejected(tmp_path: Path) -> None:
    _write_secrets(tmp_path)
    target = tmp_path / "elsewhere"
    target.write_text("a1b2c3d4e5f60718293a4b5c6d7e8f90", encoding="utf-8")
    (tmp_path / "auth_secret").unlink()
    (tmp_path / "auth_secret").symlink_to(target)
    out = _check_secrets(tmp_path)
    assert out.returncode == 1 and "regular file" in out.stdout


def test_e16_3_secrets_env_file_may_only_hold_placeholders(tmp_path: Path) -> None:
    _write_secrets(tmp_path)
    good = tmp_path / "prod-vars.txt"
    good.write_text(
        "SBAM_PORTAL_HOST=x.example.com\nPOSTGRES_PASSWORD=change-me-postgres\nOPENROUTER_API_KEY=\n",
        encoding="utf-8",
    )
    assert _check_secrets(tmp_path, "--env-file", str(good)).returncode == 0
    bad = tmp_path / "bad-vars.txt"
    bad.write_text("POSTGRES_PASSWORD=Zk9fReallyLooksReal123\n", encoding="utf-8")
    out = _check_secrets(tmp_path, "--env-file", str(bad))
    assert out.returncode == 1 and "POSTGRES_PASSWORD holds a real-looking value" in out.stdout
    assert "Zk9fReally" not in out.stdout


def test_e16_3_env_example_has_only_placeholders_for_secrets() -> None:
    for line in (COMPOSE / ".env.example").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if re.search(
            r"(PASSWORD|SECRET|TOKEN|API_KEY|SALT|ENCRYPTION_KEY|SIGNING_KEY)$",
            key.strip(),
        ):
            assert value == "" or value.startswith("change-me"), key


# --- the secret loader (compose/secrets/with-secrets.sh) ------------------------------------------

LOADER = COMPOSE / "secrets" / "with-secrets.sh"


def _run_loader(env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [SH, str(LOADER), *argv],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], **env},
    )


def test_e16_3_loader_exports_secret_from_file_and_hides_the_pointer(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "pw"
    secret.write_text("p4ss-w0rd\n", encoding="utf-8")
    out = _run_loader(
        {"DB_PASSWORD_FILE": str(secret)},
        "sh",
        "-c",
        'echo "pw=$DB_PASSWORD file=${DB_PASSWORD_FILE:-gone}"',
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "pw=p4ss-w0rd file=gone"


def test_e16_3_loader_expands_database_url_template(tmp_path: Path) -> None:
    secret = tmp_path / "pw"
    secret.write_text("abc123", encoding="utf-8")
    env = {
        "SBAM_DB_PASSWORD_FILE": str(secret),
        "DATABASE_URL_TEMPLATE": "postgresql://sbam:__SBAM_DB_PASSWORD__@postgres:5432/sbam",
    }
    out = _run_loader(env, "sh", "-c", 'echo "$DATABASE_URL ${DATABASE_URL_TEMPLATE:-gone}"')
    assert out.stdout.strip() == "postgresql://sbam:abc123@postgres:5432/sbam gone"


def test_e16_3_loader_fails_closed_on_a_missing_secret(tmp_path: Path) -> None:
    out = _run_loader({"AUTH_SECRET_FILE": str(tmp_path / "missing")}, "sh", "-c", "echo started")
    assert out.returncode == 78 and "started" not in out.stdout
    assert "missing or not readable" in out.stderr
    tpl = _run_loader(
        {"DATABASE_URL_TEMPLATE": "postgresql://sbam:__SBAM_DB_PASSWORD__@x/y"}, "true"
    )
    assert tpl.returncode != 0  # a template without its password file does not start


def test_e16_3_loader_passes_arguments_through_unchanged() -> None:
    out = _run_loader({}, "sh", "-c", 'printf "%s|" "$@"', "arg0", "a b", "c")
    assert out.stdout == "a b|c|"


# --- the realm rendered for the deployed origin ---------------------------------------------------


def test_e16_3_render_realm_rewrites_every_local_origin() -> None:
    source = (COMPOSE / "keycloak" / "realm-bullm.json").read_text(encoding="utf-8")
    rendered = render_realm.render(source, "https://portal.example.com")
    assert "localhost" not in rendered and "${WEB_PORT" not in rendered
    realm = json.loads(rendered)
    uris = [u for client in realm["clients"] for u in client.get("redirectUris", [])]
    assert uris and all(u.startswith("https://portal.example.com/") for u in uris)
    assert "localhost" in source  # the committed dev file is untouched


@pytest.mark.parametrize(
    "origin",
    [
        "http://portal.example.com",
        "https://localhost:3000",
        "https://portal.example.com/app",
        "portal.example.com",
        "https://a b",
    ],
)
def test_e16_3_render_realm_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="origin must be"):
        render_realm.render("{}", origin)


# --- drift guards: restated commands must equal what the images/base file define ------------------


def test_e16_3_deploy_commands_match_images() -> None:
    deploy = (COMPOSE / "compose.deploy.yaml").read_text(encoding="utf-8")
    dockerfile = (COMPOSE / "docker" / "server.Dockerfile").read_text(encoding="utf-8")
    prod_stage = dockerfile.split("AS prod")[1] if "AS prod" in dockerfile else dockerfile
    image_cmd = re.search(r"^CMD (\[.+\])$", prod_stage, re.MULTILINE)
    assert image_cmd
    cmd = json.loads(image_cmd.group(1))[2].replace("$SBAM_ROLE", "$$SBAM_ROLE")
    assert cmd in deploy, "x-api-command must equal the prod image CMD"
    web = re.findall(
        r"^CMD (\[.+\])$",
        (COMPOSE / "docker" / "web.Dockerfile").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert json.loads(web[-1]) == ["node", "server.js"] and "command: [node, server.js]" in deploy

    base = (COMPOSE / "compose.yaml").read_text(encoding="utf-8")
    script = re.search(
        r"entrypoint:\n(?:\s+- .+\n){2}\s+- \|\n((?:\s{8}.+\n)+)",
        base.split("temporal-schema:")[1],
    )
    assert script
    overlay = deploy.split("temporal-schema:")[1].split("environment:")[0]
    assert script.group(1).strip().splitlines() == [
        line.strip()
        for line in overlay.split("- |\n")[1].strip().splitlines()
        if not line.lstrip().startswith("#")
    ][: len(script.group(1).strip().splitlines())] or all(
        line.strip() in overlay for line in script.group(1).strip().splitlines()
    )


def test_e16_3_deploy_files_never_build_or_use_latest_or_inline_secrets() -> None:
    for name in DEPLOY_FILES:
        text = "\n".join(
            line
            for line in (COMPOSE / name).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert ":latest" not in text, name
        assert "change-me" not in text, name
        assert not re.search(r"^\s+build:\s*(?!!reset)\S", text, re.MULTILINE), name


def test_e16_3_new_variables_are_documented_with_a_default() -> None:
    documented = (COMPOSE / ".env.example").read_text(encoding="utf-8") + (
        COMPOSE / "release.env.example"
    ).read_text(encoding="utf-8")
    used: set[str] = set()
    for name in DEPLOY_FILES:
        used |= set(re.findall(r"\$\{(SBAM_[A-Z_]+)", (COMPOSE / name).read_text(encoding="utf-8")))
    caddy_vars = set(re.findall(r"\{\$(SBAM_[A-Z_]+)", CADDY))
    for var in used | caddy_vars:
        assert re.search(
            rf"^#?\s*{var}=", documented, re.MULTILINE
        ), f"{var} is not in .env.example/release.env.example"


def test_e16_3_make_compose_lint_target_exists() -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("GNU make not installed")
    out = subprocess.run(  # noqa: S603 - fixed argv
        [make, "-n", "-C", str(REPO), "compose-lint"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0 and "scripts/compose_lint.py" in out.stdout


def test_e16_3_scripts_are_executable_and_the_loader_is_world_readable() -> None:
    for name in (
        "check-secrets.sh",
        "check-cert-expiry.sh",
        "verify-public-exposure.sh",
    ):
        assert os.access(REPO / "scripts" / name, os.X_OK), name
    assert (
        LOADER.stat().st_mode & 0o444 == 0o444
    )  # containers run as uid 1000/10001 and bind-mount it


def test_a09_t06_compose_has_no_builtin_cognee_secret_default() -> None:
    text = (REPO / "compose" / "compose.yaml").read_text("utf-8")
    assert "COGNEE_USER_SECRET: ${COGNEE_USER_SECRET:-}" in text
    assert "change-me" not in text


def test_a09_t07_cognee_secret_below_32_characters_fails_the_secret_check(tmp_path: Path) -> None:
    short, placeholder = tmp_path / "short", tmp_path / "placeholder"
    short.mkdir()
    placeholder.mkdir()
    _write_secrets(short, **{"cognee_user_secret": "s3cret-value-0123456789abcdef"})  # 28 chars
    out = _check_secrets(short)
    assert out.returncode == 1 and "cognee_user_secret: shorter than 32" in out.stdout
    _write_secrets(placeholder, **{"cognee_user_secret": "change-me-" + "0" * 30})
    assert "cognee_user_secret: still a template placeholder" in _check_secrets(placeholder).stdout
