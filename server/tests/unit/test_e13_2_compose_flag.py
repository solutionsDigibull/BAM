"""E1.11-T05, left to E13.2-T02 by #42: the `sandbox-local` profile's environment flag makes a
run non-certifying. The overlay names the mode the adapter reads, the base file passes the variable
through empty, and no file a deployed environment loads ever sets the local mode."""

from pathlib import Path

import yaml
from sbam_contracts.sandbox import SandboxMode

COMPOSE = Path(__file__).resolve().parents[3] / "compose"


class ComposeLoader(yaml.SafeLoader):
    """Compose's own tags (`!reset`, `!override`) read as the value they wrap."""


def _wrapped(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    assert isinstance(node, yaml.ScalarNode)
    return None if node.value in ("null", "") else loader.construct_scalar(node)


ComposeLoader.add_constructor("!reset", _wrapped)
ComposeLoader.add_constructor("!override", _wrapped)
parse = yaml.load


def load(name: str) -> dict[str, dict[str, object]]:
    text = (COMPOSE / name).read_text(encoding="utf-8")
    document = parse(text, Loader=ComposeLoader)
    services: dict[str, dict[str, object]] = document["services"]
    return services


def environment(services: dict[str, dict[str, object]], name: str) -> dict[str, str]:
    env = services[name].get("environment", {})
    assert isinstance(env, dict)
    return {k: str(v) for k, v in env.items()}


def test_e1_11_t05_the_profile_sets_the_mode_the_adapter_reads_for_both_services() -> None:
    local = load("compose.sandbox-local.yaml")
    for service in ("worker-test", "sandbox-runner"):
        value = environment(local, service)["SANDBOX_MODE"]
        assert (
            SandboxMode(value) is SandboxMode.LOCAL_DOCKER_NONCERT
            and not SandboxMode(value).certifying
        )


def test_e13_2_the_base_file_passes_the_mode_through_and_defaults_to_none() -> None:
    env = environment(load("compose.yaml"), "worker-test")
    assert env["SANDBOX_MODE"] == "${SANDBOX_MODE:-}"  # empty = no sandbox, never a guessed mode
    assert env["SBAM_QUEUES"] == "sbam-test"


def test_e13_2_no_deployed_overlay_sets_a_mode() -> None:
    for name in (
        "compose.deploy.yaml",
        "compose.staging.yaml",
        "compose.prod.yaml",
        "compose.dev.yaml",
    ):
        services = load(name)
        env = environment(services, "worker-test") if "worker-test" in services else {}
        # the operator's env file decides, and the preflight checks it
        assert "SANDBOX_MODE" not in env, name


def test_e13_2_a_deployed_test_worker_gets_the_control_token_from_a_secret_file_only() -> None:
    env = environment(load("compose.deploy.yaml"), "worker-test")
    assert "SANDBOX_AGENT_TOKEN" not in env
    control = {k: v for k, v in env.items() if k.startswith("MOCK_CONTROL")}
    assert control == {
        "MOCK_CONTROL_TOKEN": "None",  # reset: no value in the file or the environment
        "MOCK_CONTROL_TOKEN_FILE": "/run/secrets/mock_control_token",
    }
