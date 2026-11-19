"""WG-31 (#908): Staging in Compose, checked statically (no Docker needed). The live proof is
`scripts/staging-proof.sh`, run by tests/integration/test_wg31_staging_stack.py.

T01 the default stack is unchanged (no Staging service, no Docker access, no secret value) and the
profile is opt-in; T05 no worker mounts docker.sock, only the filtering socket proxy does, and
the proxy allowlist has no exec, volumes, privileged create, bind or host network. The egress
proxy URL carries a password so clients send the deployment token (found on the live stack).
"""

import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "compose"
PROFILE = COMPOSE / "compose.staging-runtime.yaml"
BASE = ("compose.yaml", "compose.override.yaml")
SOCKET = re.compile(r"docker\.sock|docker_engine|/var/run/docker|npipe:", re.IGNORECASE)
SECRETISH = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|SIGNING_KEY|KEYS)", re.IGNORECASE)


class ComposeLoader(yaml.SafeLoader):
    """Compose's own tags read as the value they wrap."""


ComposeLoader.add_constructor("!reset", lambda loader, node: None)
ComposeLoader.add_constructor("!override", lambda loader, node: None)


def load(path: Path) -> dict[str, Any]:
    # ComposeLoader is a SafeLoader that only adds Compose's own tags
    text = path.read_text(encoding="utf-8")
    document: dict[str, Any] = yaml.load(text, Loader=ComposeLoader)  # noqa: S506
    return document


def services(path: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = load(path).get("services") or {}
    return found


def mounts(service: dict[str, Any]) -> list[str]:
    out = [
        str(v) if not isinstance(v, dict) else str(v.get("source", ""))
        for v in service.get("volumes", [])
    ]
    env = service.get("environment") or {}
    out += [str(v) for v in (env.values() if isinstance(env, dict) else env)]
    return out


def test_wg31_t01_the_default_stack_has_no_staging_service_and_no_docker_access() -> None:
    for name in BASE:
        for service, body in services(COMPOSE / name).items():
            assert not any(
                SOCKET.search(m) for m in mounts(body)
            ), f"{name}: {service} reaches Docker"
            env = body.get("environment") or {}
            assert (
                not isinstance(env, dict) or "STAGING_RUNTIME" not in env
            ), f"{service} turns Staging on"
    base = services(COMPOSE / "compose.yaml")
    assert not {"docker-socket-proxy", "staging-egress-proxy", "docker-cli-init"} & set(base)


def test_wg31_t01_the_worker_default_runtime_is_none_so_make_up_stays_healthy() -> None:
    from sbam_workers.settings import WorkerSettings

    settings = WorkerSettings(database_url="postgresql://x:y@h/db", temporal_host="t:7233")
    assert settings.staging_runtime == "none" and settings.image_builder == "none"
    assert settings.staging_network == "sbam-staging"


def test_wg31_t01_the_profile_is_opt_in_through_make_profiles() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    valid = re.search(r"^VALID_PROFILES := (.+)$", makefile, re.MULTILINE)
    assert valid is not None and "staging-runtime" in valid[1].split()
    assert PROFILE.is_file()
    assert "compose/compose.$(p).yaml" in makefile  # one file per profile name
    default_files = re.search(r"-f compose/compose.yaml -f compose/compose.override.yaml", makefile)
    assert default_files is not None and "staging-runtime" not in default_files[0]


def test_wg31_the_profile_carries_no_secret_value_only_substitutions() -> None:
    for service, body in services(PROFILE).items():
        env = body.get("environment") or {}
        for key, value in env.items():
            if SECRETISH.search(key):
                assert re.fullmatch(
                    r"\$\{[A-Z_]+:-\}", str(value)
                ), f"{service}.{key} holds a value"


def test_wg31_the_profile_turns_the_runtime_on_for_the_commit_worker_with_a_restricted_host() -> (
    None
):
    found = services(PROFILE)
    env = found["worker-certify"]["environment"]
    assert env["STAGING_RUNTIME"] == "docker"
    assert env["DOCKER_HOST"] == "tcp://docker-socket-proxy:2375"
    assert env["STAGING_EGRESS_PROXY_URL"] == "http://staging-egress-proxy:3128"
    assert env["STAGING_EGRESS_POLICY_DIR"] == "/var/sbam/egress"
    for key in (
        "STAGING_NETWORK",
        "STAGING_DOCKER_TIMEOUT_SECONDS",
        "SECRETS_KEYS",
        "BIFROST_SETUP_TOKEN",
    ):
        assert key in env
    assert (
        found["worker-build"]["environment"]["IMAGE_BUILDER"] == "${IMAGE_BUILDER:-none}"
    )  # opt-in
    for worker in ("worker-certify", "worker-build"):
        assert "sbam_docker" in found[worker]["networks"]
    network = load(PROFILE)["networks"]
    assert (
        network["sbam_docker"]["internal"] is True and network["sbam_staging"]["internal"] is True
    )
    assert "sbam_staging" in found["sbam-api"]["networks"]  # the API reaches the container
    egress = found["staging-egress-proxy"]
    assert "sbam_edge" in egress["networks"] and "sbam_staging" in egress["networks"]
    assert egress["healthcheck"]["test"] and "bifrost" in found


def test_wg31_t05_no_worker_mounts_docker_sock_in_any_compose_file_or_the_profile() -> None:
    offenders: list[str] = []
    for path in sorted(COMPOSE.glob("compose*.yaml")):
        for service, body in services(path).items():
            if SOCKET.search(" ".join(mounts(body))) and service != "docker-socket-proxy":
                offenders.append(f"{path.name}: {service}")
    assert offenders == [], offenders


def test_wg31_t05_only_the_socket_proxy_holds_the_socket_on_an_internal_network() -> None:
    found = services(PROFILE)
    holders = [name for name, body in found.items() if SOCKET.search(" ".join(mounts(body)))]
    assert holders == ["docker-socket-proxy"]
    proxy = found["docker-socket-proxy"]
    assert proxy["networks"] == ["sbam_docker"] and not proxy.get("ports")
    assert "@sha256:" in proxy["image"] and "docker-proxy/haproxy.cfg.template" in " ".join(
        proxy["volumes"]
    )
    assert not any(name.startswith("worker-") for name in holders)


def test_wg31_t05_the_proxy_allowlist_has_no_exec_volumes_privileged_bind_or_host_network() -> None:
    config = (COMPOSE / "docker-proxy" / "haproxy.cfg.template").read_text(encoding="utf-8")
    rules = [ln.strip() for ln in config.splitlines() if ln.strip().startswith("http-request")]
    assert rules[-1] == "http-request deny"  # the default is no
    allowed = " ".join(r for r in rules if r.startswith("http-request allow"))
    for forbidden in (
        "exec",
        "attach",
        "volumes",
        "push",
        "images/create",
        "swarm",
        "events",
        "commit",
    ):
        assert forbidden not in allowed, forbidden
    assert "http-request deny if { req.hdr(Upgrade) -m found }" in rules  # no hijacked streams
    # a create must be a stg- container on the Staging network and must not ask for host reach
    create = next(r for r in rules if "ctr_create" in r and "allow" in r)
    for needed in (
        "create_named",
        "create_net",
        "!create_flag",
        "!create_list",
        "!create_ns",
        "!create_sec",
    ):
        assert needed in create
    for key in (
        "Privileged",
        "Binds",
        "Mounts",
        "Devices",
        "CapAdd",
        "PidMode",
        "UsernsMode",
        "unconfined",
    ):
        assert key in config
    assert re.search(r"ctr_start .*stg-", config) and re.search(r"ctr_remove .*stg-", config)


def test_wg31_the_egress_proxy_url_carries_a_password_so_clients_send_the_token() -> None:
    """urllib and requests send Proxy-Authorization only for `user:password@`; `user@` sent nothing
    and the proxy answered 403 `unknown deployment token` on the live stack."""
    from sbam_workers.staging.runtime import DeployerStagingRuntime, StagingConfig

    runtime = DeployerStagingRuntime.__new__(DeployerStagingRuntime)
    policies = _Policies()
    proxy = "http://staging-egress-proxy:3128"
    runtime.config = StagingConfig(proxy_url=proxy, policy_dir=policies)  # type: ignore[arg-type]
    env, enforced = runtime._egress("stg-x", ["api.example.com"])
    assert enforced and env["HTTP_PROXY"] == env["HTTPS_PROXY"]
    handler = urllib.request.ProxyHandler({"http": env["HTTP_PROXY"]})
    request = urllib.request.Request("http://api.example.com/")
    handler.proxy_open(request, env["HTTP_PROXY"], "http")
    header = request.get_header("Proxy-authorization")
    assert header is not None and header.startswith("Basic ")
    import base64

    token = base64.b64decode(header.split(" ", 1)[1]).decode().split(":", 1)[0]
    assert re.fullmatch(r"[0-9a-f]{32}", token)


class _Policies:
    def write(self, token: str, deployment: str, hosts: Any) -> None:
        return None


def test_wg32_bifrost_and_the_commit_worker_join_the_staging_network_only_in_the_profile() -> None:
    """#909, ADR 0039: Bifrost connects to the Staging container by name; the worker waits."""
    found = services(PROFILE)
    assert "sbam_staging" in found["bifrost"]["networks"]
    assert "sbam_staging" in found["worker-certify"]["networks"]
    assert found["worker-certify"]["environment"]["STAGING_READY_TIMEOUT_SECONDS"]
    for name in BASE:  # the default stack is unchanged: nothing there is on the Staging network
        for service, body in services(COMPOSE / name).items():
            assert "sbam_staging" not in (body.get("networks") or []), f"{name}: {service}"
    assert "sbam_staging" not in (load(COMPOSE / "compose.yaml").get("networks") or {})
    assert not SECRETISH.search(
        "".join(
            str(v)
            for v in (found["bifrost"].get("environment") or {}).values()
            if "${" not in str(v)
        )
    )
