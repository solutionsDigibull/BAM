#!/usr/bin/env python3
"""Lint the deployed Compose environments (E16.3, #142, TD 20.1/20.5).

Resolves compose.yaml + compose.deploy.yaml + compose.<env>.yaml with `docker compose config` (it starts
nothing) for dev, staging and prod, then checks the resolved result:

  R1 images     every image is pinned `name@sha256:<64 hex>`, never `:latest`, never built on the host
  R3 ports      only the proxy publishes 80/443 and only prod publishes them on all addresses; the ops UIs
                (Temporal UI, Keycloak, Grafana, Langfuse, PostHog) publish on a specific address only;
                postgres, temporal, api, tusd, bifrost, web and clamav publish nothing
  R4 secrets    no secret-looking environment variable carries an inline value (`*_FILE` pointers only)
  R5 limits     workers have cpus and mem_limit
  R6 order      depends_on uses service_healthy / service_completed_successfully only; api-migrate runs
                before sbam-api and the workers; a healthy-gated service has a healthcheck
  R7 networks   sbam_internal is internal; data and app services are not on the internet-facing network
  R8 secrets    Docker secrets are `file:` secrets under SBAM_SECRETS_DIR with mode 0400, every `*_FILE`
                points at a secret granted to that service
  R9 env flags  SBAM_ENV, real LLM provider and no dev auth / seed password / dev tools outside dev

It also prints the digest manifest (`--manifest`) and fails when the environments disagree on an image.
Services that only exist through the obs/analytics profiles carry known debt owned by #140/#141 (inline
secrets, a :latest minio tag); those findings are warnings, never errors, and are listed as such.

    python3 scripts/compose_lint.py                 # all environments, core
    python3 scripts/compose_lint.py --profiles obs,analytics --env prod
    python3 scripts/compose_lint.py --manifest      # digest manifest per environment (JSON)
    python3 scripts/compose_lint.py --release release-1.2.3.env   # a real release file: no placeholder digests
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "compose"
ENVS = ("dev", "staging", "prod")
PROFILE_FILES = {
    "obs": ("compose.obs.yaml", "compose.deploy.obs.yaml"),
    "analytics": ("compose.analytics.yaml", "compose.deploy.analytics.yaml"),
}
DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
ZERO_DIGEST = "sha256:" + "0" * 64
SECRET_NAME_RE = re.compile(
    r"(PASSWORD|PASSWD|PWD|SECRET|TOKEN|API_KEY|SIGNING_KEY|ENCRYPTION_KEY|SALT)",
    re.IGNORECASE,
)
URL_CRED_RE = re.compile(r"://[^/\s:@]+:([^/\s@]+)@")
DB_PLACEHOLDER = "__SBAM_DB_PASSWORD__"

# Services that may publish a host port, and the container ports allowed for them.
OPS_PORTS = {
    "temporal-ui": {8080},
    "keycloak": {8080},
    "otel-collector": {
        3000
    },  # Grafana (obs profile); the OTLP ports are not published when deployed
    "langfuse-web": {3000},  # obs profile
    "posthog": {8000},  # analytics profile
}
PROXY = "proxy"
PROXY_PORTS = {80, 443}
WILDCARD_HOSTS = {"", "0.0.0.0", "::", None}
# Never on the internet-facing network in a deployed environment.
INTERNAL_ONLY_PREFIXES = ("worker-",)
INTERNAL_ONLY = {"postgres", "temporal", "sbam-api", "api-migrate", "tusd", "sbam-web"}
ONE_SHOT = {"api-migrate", "storage-init", "temporal-schema", "temporal-namespace"}
# Healthcheck defined by the image, not by compose.yaml: service -> Dockerfile under compose/docker.
IMAGE_HEALTHCHECK = {
    "sbam-web": "web.Dockerfile",
    "sbam-llm-mock": "llm-mock.Dockerfile",
}
DEV_ONLY_SERVICES = {"keycloak-seed", "api-seed", "mailpit"}
ALLOWED_SOURCE_BINDS = ("/tests/llm-mock",)  # fixtures of the dev mock upstream, read-only


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    rule: str
    service: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.rule} {self.service}: {self.message}"


def _env_pairs(service: dict[str, Any]) -> dict[str, str | None]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs: dict[str, str | None] = {}
        for item in env:
            key, _, value = str(item).partition("=")
            pairs[key] = value
        return pairs
    return {str(k): (None if v is None else str(v)) for k, v in env.items()}


def _mode_is_0400(mode: Any) -> bool:
    return str(mode) in {"0400", "400"} or mode == 0o400


def manifest(config: dict[str, Any]) -> dict[str, str]:
    """service -> image for every service of a resolved config."""
    return {name: str(svc.get("image", "")) for name, svc in sorted(config["services"].items())}


def lint_config(
    config: dict[str, Any], env: str, core_services: set[str] | None = None
) -> list[Finding]:
    """Check one resolved environment. `core_services` marks the services that are not profile debt."""
    findings: list[Finding] = []
    services: dict[str, Any] = config["services"]
    declared_secrets: dict[str, Any] = config.get("secrets") or {}
    core = core_services if core_services is not None else set(services)

    def add(rule: str, name: str, message: str) -> None:
        # Profile-only services carry known debt owned by #140/#141: warn, do not fail.
        level = "error" if name in core else "warning"
        findings.append(Finding(level, rule, name, message))

    for name, svc in sorted(services.items()):
        image = str(svc.get("image", ""))
        env_vars = _env_pairs(svc)

        # R1 images
        if not image:
            add("R1", name, "has no image")
        else:
            repo = image.split("@", 1)[0]
            if repo.endswith(":latest") or image.endswith(":latest"):
                add("R1", name, f"image uses :latest ({image})")
            if not DIGEST_RE.match(image):
                add("R1", name, f"image is not pinned by digest ({image})")
        if svc.get("build"):
            add(
                "R1",
                name,
                "builds on the host; deployed images come from the release digests",
            )

        # R3 ports
        for port in svc.get("ports") or []:
            host = port.get("host_ip")
            target = int(port["target"])
            published = int(port["published"])
            if name == PROXY:
                if {published, target} - PROXY_PORTS or published != target:
                    add(
                        "R3",
                        name,
                        f"proxy publishes {published}:{target}, only 80 and 443 allowed",
                    )
                elif env != "prod" and host in WILDCARD_HOSTS:
                    add(
                        "R3",
                        name,
                        f"{env} proxy must not be public (bound to {host or 'all'})",
                    )
                elif env == "prod" and host != "0.0.0.0":
                    add("R3", name, f"prod proxy must publish on 0.0.0.0, got {host}")
            elif name in OPS_PORTS:
                if target not in OPS_PORTS[name]:
                    add(
                        "R3",
                        name,
                        f"publishes container port {target}, not an ops UI port",
                    )
                if host in WILDCARD_HOSTS:
                    add(
                        "R3",
                        name,
                        "ops UI published on all addresses; bind it to SBAM_OPS_BIND",
                    )
            else:
                add(
                    "R3",
                    name,
                    f"must not publish a host port (publishes {published}:{target})",
                )

        # R4 inline secrets
        for key, value in env_vars.items():
            if key.endswith("_FILE") or not value:
                continue
            if SECRET_NAME_RE.search(key):
                add(
                    "R4",
                    name,
                    f"{key} carries an inline value; use Docker secrets (`{key}_FILE`)",
                )
            elif "change-me" in value:
                add("R4", name, f"{key} carries a placeholder secret value")
            for cred in URL_CRED_RE.findall(value):
                if cred != DB_PLACEHOLDER:
                    add("R4", name, f"{key} embeds a password in a URL")

        # R5 limits
        has_limits = bool(svc.get("cpus")) and bool(svc.get("mem_limit"))
        if name.startswith("worker-") and not has_limits:
            add("R5", name, "worker without cpus and mem_limit")
        elif not has_limits and name not in ONE_SHOT:
            findings.append(Finding("warning", "R5", name, "no cpus/mem_limit"))

        # R6 order
        for dep, spec in (svc.get("depends_on") or {}).items():
            cond = spec.get("condition")
            if cond not in {"service_healthy", "service_completed_successfully"}:
                add(
                    "R6",
                    name,
                    f"depends_on {dep} uses {cond}; only healthy/completed allowed",
                )
            if cond == "service_healthy":
                target = services.get(dep, {})
                if not target.get("healthcheck") and dep not in IMAGE_HEALTHCHECK:
                    add(
                        "R6",
                        name,
                        f"waits for {dep} to be healthy but {dep} has no healthcheck",
                    )
            if dep not in services:
                add("R6", name, f"depends_on {dep}, which is not in this environment")
        if name == "sbam-api" or name.startswith("worker-"):
            cond = ((svc.get("depends_on") or {}).get("api-migrate") or {}).get("condition")
            if cond != "service_completed_successfully":
                add("R6", name, "must wait for api-migrate to complete successfully")

        # R7 networks
        nets = set((svc.get("networks") or {}).keys())
        if not nets and svc.get("network_mode") is None:
            add("R7", name, "attached to no network")
        if (
            name in INTERNAL_ONLY or name.startswith(INTERNAL_ONLY_PREFIXES)
        ) and "sbam_edge" in nets:
            add(
                "R7",
                name,
                "must only be on sbam_internal, not the internet-facing network",
            )

        # R8 secrets
        granted = {}
        for ref in svc.get("secrets") or []:
            ref = ref if isinstance(ref, dict) else {"source": ref, "target": ref}
            granted[str(ref.get("target") or ref["source"])] = ref
            if ref["source"] not in declared_secrets:
                add("R8", name, f"uses undeclared secret {ref['source']}")
            if not _mode_is_0400(ref.get("mode")):
                add(
                    "R8",
                    name,
                    f"secret {ref['source']} must have mode 0400 (got {ref.get('mode')})",
                )
        for key, value in env_vars.items():
            if key.endswith("_FILE") and value and value.startswith("/run/secrets/"):
                if value.rsplit("/", 1)[1] not in granted:
                    add(
                        "R8",
                        name,
                        f"{key} points at {value}, which is not granted to this service",
                    )

        # R9 environment flags and dev-only leftovers
        if name in DEV_ONLY_SERVICES:
            add("R9", name, "dev-only service present in a deployed environment")
        if "SEED_USER_PASSWORD" in env_vars:
            add(
                "R9",
                name,
                "SEED_USER_PASSWORD must not exist in a deployed environment",
            )
        for mount in svc.get("volumes") or []:
            source = str(mount.get("source", ""))
            if mount.get("type") == "bind" and (ROOT / "server") in Path(source).parents:
                if not source.endswith(ALLOWED_SOURCE_BINDS):
                    add(
                        "R9",
                        name,
                        f"source mount {source} (deployed images carry their own code)",
                    )
        if name == "keycloak" and any("start-dev" in str(c) for c in (svc.get("command") or [])):
            add("R9", name, "keycloak must run `start`, not `start-dev`")

    findings += _env_rules(services, env, core)
    for secret, spec in declared_secrets.items():
        if "file" not in spec or spec.get("environment") or spec.get("content"):
            findings.append(Finding("error", "R8", secret, "secret must be a `file:` secret"))
        elif not str(spec["file"]).startswith("/etc/sbam/secrets/"):
            findings.append(
                Finding(
                    "error",
                    "R8",
                    secret,
                    f"file {spec['file']} not under SBAM_SECRETS_DIR",
                )
            )
    return findings


def _env_rules(services: dict[str, Any], env: str, core: set[str]) -> list[Finding]:
    out: list[Finding] = []

    def err(name: str, message: str) -> None:
        out.append(Finding("error", "R9", name, message))

    api = _env_pairs(services.get("sbam-api", {}))
    expected = {"dev": "development", "staging": "staging", "prod": "production"}[env]
    if api.get("SBAM_ENV") != expected:
        err("sbam-api", f"SBAM_ENV must be {expected}, got {api.get('SBAM_ENV')}")
    if api.get("ENABLE_PING") != "false":
        err("sbam-api", "ENABLE_PING must be false in a deployed environment")
    if not str(api.get("KEYCLOAK_ISSUER", "")).startswith("https://"):
        err("sbam-api", "KEYCLOAK_ISSUER must be the public https URL")
    web = _env_pairs(services.get("sbam-web", {}))
    if web.get("SBAM_DEV_AUTH"):
        err("sbam-web", "SBAM_DEV_AUTH must be empty (dev role picker off)")
    if not str(web.get("AUTH_URL", "")).startswith("https://"):
        err("sbam-web", "AUTH_URL must be the public https URL")
    kc = _env_pairs(services.get("keycloak", {}))
    if not str(kc.get("KC_HOSTNAME", "")).startswith("https://"):
        err("keycloak", "KC_HOSTNAME must be the public https URL")
    if env != "dev":
        for name in sorted(n for n in services if n.startswith("worker-")):
            worker_env = _env_pairs(services[name]).get("SBAM_ENV")
            if worker_env != expected:  # an unset one reads as development: stubs would run (A-01)
                err(name, f"SBAM_ENV must be {expected}, got {worker_env}")
        if "sbam-llm-mock" in services:
            err("sbam-llm-mock", f"the mock LLM upstream must not run in {env}")
        for name in ["sbam-api"] + sorted(n for n in services if n.startswith("worker-")):
            provider = _env_pairs(services[name]).get("LLM_PROVIDER")
            if provider != "bifrost":
                err(name, f"LLM_PROVIDER must be bifrost in {env}, got {provider}")
        mounts = [m.get("source", "") for m in services.get("bifrost", {}).get("volumes") or []]
        if "bifrost" in services and not any(
            str(m).endswith("config.bifrost.json") for m in mounts
        ):
            err("bifrost", "must mount config.bifrost.json (real OpenRouter models)")
    return out


def compare_manifests(manifests: dict[str, dict[str, str]]) -> list[Finding]:
    """The digest manifest of every service must be equal in every environment that runs it."""
    out: list[Finding] = []
    for service in sorted({s for m in manifests.values() for s in m}):
        images = {env: m[service] for env, m in manifests.items() if service in m}
        if len(set(images.values())) > 1:
            out.append(
                Finding(
                    "error",
                    "T03",
                    service,
                    f"images differ between environments: {images}",
                )
            )
    return out


def synth_env_file(directory: Path, extra: dict[str, str] | None = None) -> Path:
    """Env file used for resolving: .env.example + release.env.example, no real values involved."""
    values: dict[str, str] = {}
    for source in (COMPOSE_DIR / ".env.example", COMPOSE_DIR / "release.env.example"):
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key] = value
    values["SBAM_REALM_FILE"] = str(directory / "realm-bullm.json")
    values.update(extra or {})
    path = directory / "lint-vars.txt"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def resolve(env: str, profiles: list[str], env_file: Path) -> dict[str, Any]:
    files = ["compose.yaml", "compose.deploy.yaml"]
    for profile in profiles:
        files += PROFILE_FILES[profile]
    files.append(f"compose.{env}.yaml")
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for name in files:
        cmd += ["-f", str(COMPOSE_DIR / name)]
    cmd += ["config", "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"docker compose config failed for {env}: {proc.stderr.strip()}")
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def check_release_file(path: Path) -> list[Finding]:
    """A real release file: every SBAM_*_IMAGE is a digest and none is the template's zero digest."""
    out: list[Finding] = []
    seen: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            seen[key.strip()] = value.strip()
    for key in (
        "SBAM_API_IMAGE",
        "SBAM_WORKER_IMAGE",
        "SBAM_WEB_IMAGE",
        "SBAM_MOCK_API_IMAGE",
    ):
        value = seen.get(key, "")
        if not DIGEST_RE.match(value):
            out.append(
                Finding(
                    "error",
                    "R1",
                    key,
                    f"not a name@sha256:<digest> reference ({value!r})",
                )
            )
        elif value.endswith(ZERO_DIGEST):
            out.append(Finding("error", "R1", key, "still the template's placeholder digest"))
        if value.split("@", 1)[0].endswith(":latest"):
            out.append(Finding("error", "R1", key, "uses :latest"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", action="append", choices=ENVS, help="environment (default: all)")
    parser.add_argument("--profiles", default="", help="comma separated: obs,analytics")
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="print the digest manifest per environment",
    )
    parser.add_argument("--release", type=Path, help="validate a real release env file")
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    args = parser.parse_args(argv)

    if args.release:
        problems = check_release_file(args.release)
        for finding in problems:
            print(finding)
        return 1 if problems else 0
    if shutil.which("docker") is None:
        print(
            "compose_lint: docker is required (`docker compose config` starts nothing)",
            file=sys.stderr,
        )
        return 2
    profiles = [p for p in args.profiles.split(",") if p]
    unknown = set(profiles) - set(PROFILE_FILES)
    if unknown:
        parser.error(f"unknown profiles: {sorted(unknown)}")
    envs = args.env or list(ENVS)

    findings: list[Finding] = []
    manifests: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        env_file = synth_env_file(Path(tmp))
        for env in envs:
            core_cfg = resolve(env, [], env_file)
            cfg = resolve(env, profiles, env_file) if profiles else core_cfg
            manifests[env] = manifest(cfg)
            for finding in lint_config(cfg, env, set(core_cfg["services"])):
                findings.append(
                    Finding(
                        finding.level,
                        finding.rule,
                        f"{env}/{finding.service}",
                        finding.message,
                    )
                )
    if len(manifests) > 1:
        findings += compare_manifests(manifests)

    if args.manifest:
        print(json.dumps(manifests, indent=2, sort_keys=True))
    for finding in findings:
        print(finding, file=sys.stderr if args.manifest else sys.stdout)
    errors = [f for f in findings if f.level == "error" or args.strict]
    summary = f"compose_lint: {len(errors)} error(s), {len(findings) - len(errors)} warning(s) in {', '.join(envs)}"
    print(summary, file=sys.stderr if args.manifest else sys.stdout)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
