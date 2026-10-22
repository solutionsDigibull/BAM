"""The prompt registry (E6.1, TD §5.4): versioned prompt files whose hashes are pinned in a
manifest.

Layout: `prompts/<id>/v<semver>.md` (the prompt), `prompts/<id>/meta.yaml` (`alias`, `description`)
and
`prompts/manifest.json` (`{"<id>/<version>": "<sha256>"}`). Loading checks all three, so a prompt
that was
edited without a version bump, a file nobody registered and a manifest entry without a file all
stop the
worker at startup (`PROMPT_HASH_MISMATCH`, `PromptNotFound`), never in the middle of a run.

Change a prompt by adding a new `v<semver>.md` and re-running
`python -m sbam_workers.llm.registry --write` to pin it. Never edit a released version.
"""

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).parent / "prompts"
MANIFEST = "manifest.json"
_VERSION_FILE = re.compile(r"^v(\d+\.\d+\.\d+)\.md$")


class PromptNotFound(LookupError):  # noqa: N818 - fixed name in E6.1
    code = "PROMPT_NOT_FOUND"


class PromptHashMismatch(RuntimeError):  # noqa: N818
    code = "PROMPT_HASH_MISMATCH"


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    text: str
    sha256: str
    alias: str


def prompt_sha256(text: str) -> str:
    """Hash of the text with LF line ends, so a Windows checkout hashes like a Linux one."""
    return hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()


def _semver(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _files(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(root.glob("*/v*.md")):
        match = _VERSION_FILE.match(path.name)
        if match is None:
            raise PromptHashMismatch(f"{path.name}: prompt files are named v<major.minor.patch>.md")
        found[f"{path.parent.name}/{match.group(1)}"] = path
    return found


def build_manifest(root: Path = PROMPTS_DIR) -> dict[str, str]:
    return {
        key: prompt_sha256(path.read_text(encoding="utf-8")) for key, path in _files(root).items()
    }


class PromptRegistry:
    def __init__(self, root: Path = PROMPTS_DIR) -> None:
        self.root = root
        manifest_path = root / MANIFEST
        manifest: dict[str, str] = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        )
        files = _files(root)
        for key in manifest:
            if key not in files:
                raise PromptNotFound(f"{key} is in the manifest but its file is missing")
        self._prompts: dict[str, dict[str, Prompt]] = {}
        for key, path in files.items():
            if key not in manifest:
                raise PromptHashMismatch(f"{key} is not in {MANIFEST}; pin it with --write")
            text = path.read_text(encoding="utf-8")
            digest = prompt_sha256(text)
            if digest != manifest[key]:
                raise PromptHashMismatch(
                    f"{key} changed without a version bump (manifest {manifest[key][:12]}, "
                    f"file {digest[:12]}); add a new version instead of editing a released one"
                )
            prompt_id, version = key.split("/")
            meta = yaml.safe_load((path.parent / "meta.yaml").read_text(encoding="utf-8")) or {}
            self._prompts.setdefault(prompt_id, {})[version] = Prompt(
                prompt_id, version, text, digest, str(meta.get("alias", ""))
            )

    def get(self, prompt_id: str, version: str | None = None) -> Prompt:
        versions = self._prompts.get(prompt_id)
        if not versions:
            raise PromptNotFound(f"unknown prompt {prompt_id!r}")
        if version is None:
            version = max(versions, key=_semver)
        if version not in versions:
            raise PromptNotFound(f"prompt {prompt_id!r} has no version {version!r}")
        return versions[version]

    def ids(self) -> list[str]:
        return sorted(self._prompts)


def main(argv: list[str]) -> int:
    if "--write" in argv:
        (PROMPTS_DIR / MANIFEST).write_text(
            json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    PromptRegistry()  # raises on any mismatch
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
