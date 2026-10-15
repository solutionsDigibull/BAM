"""Template errors (TD §12.3, TD §18 E17). One exception, a stable code, every finding."""

from enum import StrEnum
from typing import Final

# State-machine event for a template defect: BUILDING --template_error--> BUILD_ERROR (TD §14).
BUILD_ERROR_EVENT: Final = "template_error"


class TemplateErrorCode(StrEnum):
    """Stable codes. Add new ones here; never rename or reuse one."""

    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"  # no such name/version, or not in the registry
    TEMPLATE_INVALID = "TEMPLATE_INVALID"  # template.yaml or the file tree is malformed
    TEMPLATE_HASH_MISMATCH = "TEMPLATE_HASH_MISMATCH"  # released tree changed without a bump
    TEMPLATE_UNSUPPORTED = "TEMPLATE_UNSUPPORTED"  # version does not support this ISATVON version
    TEMPLATE_PARAM_INVALID = "TEMPLATE_PARAM_INVALID"  # render params failed validation
    TEMPLATE_RENDER_ERROR = "TEMPLATE_RENDER_ERROR"  # Jinja2 failed (platform bug, E17)


class TemplateError(Exception):
    """Anything that stops a skeleton from being rendered. Nothing is written when it is raised.

    Every code maps to the `template_error` event (BUILD_ERROR): the Sub-Skill build stops and
    the platform team fixes the template or the mapping; the FDE cannot retry it away.
    """

    event: Final = BUILD_ERROR_EVENT

    def __init__(
        self, code: TemplateErrorCode, message: str, findings: list[str] | None = None
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.findings = findings if findings is not None else [message]
