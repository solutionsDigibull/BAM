"""Sub-Skill and Workflow manifests, and gate results (E9.1, E9.5, TD §7, §11): written by the build
stages; read back by the Manifest viewer (E9.6, `router`)."""

from sbam_api.manifests.router import router

__all__ = ["router"]
