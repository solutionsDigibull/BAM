"""The Filetype platform configuration (TD §5.2, issue E3.7).

Two parts, joined by row label:
  * `frozen_filetypes.json`: the 43 rows of the frozen BAM 0.2 Filetype tab (O-23), as shipped in
    the consolidated workbook (a test keeps it equal to the workbook);
  * `filetypes.yaml`: what SBAM adds on top: `mvp_enabled`, `loader`, `extensions`, `magic`,
    `intent_class` and `default_route`. It is never written back into the frozen tab.

`load()` validates both and raises `ConfigError`; the API module imports it at startup, so a bad
configuration stops the API before it serves anything. `allowlist()` is the upload allowlist: the
extensions of the MVP-enabled types (tusd pre-create, E4.2; detection, E4.4).
"""

import json
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sbam_contracts.masters import FiletypePayload

HERE: Final = Path(__file__).resolve().parent
CONFIG_FILE: Final = HERE / "filetypes.yaml"
FROZEN_FILE: Final = HERE / "frozen_filetypes.json"
FILETYPE_COUNT: Final = 43

# The MVP loaders of TD §5.2 (implemented in the ingestion module, E4.x).
LOADERS: Final = frozenset(
    {"text", "email", "structured", "source-code", "tabular", "docx", "pdf-text", "archive", "ocr"}
)
# Types with no MVP parser (TD §5.2 "Future"): CAD, PCB, media, Google, Web, parquet. Never enabled.
FUTURE: Final = frozenset(
    f"Filetype #{n}" for n in (14, 17, 20, 25, 26, 27, 28, 29, 32, 33, 36, 37, 39, 40, 41, 42, 43)
)

Route = Literal["Organization", "Rules", "Agents", "Skills"]


class ConfigError(RuntimeError):
    """The Filetype configuration is invalid; the API must not start."""


class FiletypeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mvp_enabled: bool
    loader: str | None
    extensions: list[str]
    magic: list[str]
    intent_class: str = Field(min_length=1)
    default_route: Route


class Filetype(BaseModel):
    """One frozen row joined with its configuration."""

    model_config = ConfigDict(frozen=True)

    row_label: str
    frozen: FiletypePayload
    config: FiletypeConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"can't read {path.name}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("filetypes"), dict):
        raise ConfigError(f"{path.name} must hold a `filetypes:` mapping")
    filetypes: dict[str, Any] = data["filetypes"]
    return filetypes


def _read_frozen(path: Path) -> dict[str, FiletypePayload]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["row_label"]: FiletypePayload.model_validate(
            {k: v for k, v in row.items() if k != "row_label"}
        )
        for row in rows
    }


def validate(raw: dict[str, Any], frozen: dict[str, FiletypePayload]) -> list[Filetype]:
    """Every rule of E3.7; raises ConfigError naming the first broken one."""
    if len(raw) != FILETYPE_COUNT:
        raise ConfigError(f"expected {FILETYPE_COUNT} filetypes, found {len(raw)}")
    if len(frozen) != FILETYPE_COUNT:
        raise ConfigError(f"the frozen Filetype tab must hold {FILETYPE_COUNT} rows")
    unknown = sorted(set(raw) - set(frozen))
    if unknown:
        raise ConfigError(f"unknown row label(s): {', '.join(unknown)}")
    out: list[Filetype] = []
    for label, frozen_row in frozen.items():
        try:
            config = FiletypeConfig.model_validate(raw[label])
        except ValidationError as exc:
            raise ConfigError(f"{label}: {exc.errors()[0]['msg']}") from None
        if config.loader is not None and config.loader not in LOADERS:
            raise ConfigError(f"{label}: unknown loader {config.loader!r}")
        if config.mvp_enabled and config.loader is None:
            raise ConfigError(f"{label}: enabled without a loader")
        if config.mvp_enabled and label in FUTURE:
            raise ConfigError(f"{label} ({frozen_row.file_format}) is a Future type: never enabled")
        out.append(Filetype(row_label=label, frozen=frozen_row, config=config))
    return sorted(out, key=lambda f: int(f.row_label.rpartition("#")[2]))


@cache
def load(config_file: Path = CONFIG_FILE, frozen_file: Path = FROZEN_FILE) -> tuple[Filetype, ...]:
    return tuple(validate(_read_yaml(config_file), _read_frozen(frozen_file)))


def allowlist(filetypes: tuple[Filetype, ...] | None = None) -> frozenset[str]:
    """Upload extensions: those of the MVP-enabled types (lower case, no dot)."""
    return frozenset(
        ext.lower()
        for f in (filetypes if filetypes is not None else load())
        if f.config.mvp_enabled
        for ext in f.config.extensions
    )


def enabled_rows(filetypes: tuple[Filetype, ...] | None = None) -> dict[str, Filetype]:
    """Extension -> the MVP-enabled Filetype row for it (the first row when two share one: a
    `.pdf` is the text-based row until the loader finds no text layer and routes it to OCR)."""
    rows: dict[str, Filetype] = {}
    for filetype in filetypes if filetypes is not None else load():
        if not filetype.config.mvp_enabled or filetype.config.loader is None:
            continue
        for ext in filetype.config.extensions:
            rows.setdefault(ext.lower(), filetype)
    return rows
