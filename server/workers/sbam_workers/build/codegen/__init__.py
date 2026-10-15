"""Skeleton codegen for Sub-Skills (TD §12.3): hashed templates -> rendered FastMCP skeleton.

E11.1 owns this package's store, params and render layers. The LLM body assembler (E11.2) and
the AST gates (E11.3) sit next to them and consume the slot names declared in template.yaml.
"""

from sbam_workers.build.codegen.errors import BUILD_ERROR_EVENT, TemplateError, TemplateErrorCode
from sbam_workers.build.codegen.params import params_from_manifest, validate_params
from sbam_workers.build.codegen.render import (
    RenderedSkeleton,
    render_manifest,
    render_skeleton,
    write_skeleton,
)
from sbam_workers.build.codegen.store import LoadedTemplate, load_template, tree_sha256

__all__ = [
    "BUILD_ERROR_EVENT",
    "LoadedTemplate",
    "RenderedSkeleton",
    "TemplateError",
    "TemplateErrorCode",
    "load_template",
    "params_from_manifest",
    "render_manifest",
    "render_skeleton",
    "tree_sha256",
    "validate_params",
    "write_skeleton",
]
