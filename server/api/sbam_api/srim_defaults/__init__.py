"""Per-tenant SRIM runtime defaults (E9.1b, O-19): what the manifest builder writes into
`srim_runtime_definition`. Set by a tenant Admin, never by the LLM."""

from sbam_api.srim_defaults.router import router

__all__ = ["router"]
