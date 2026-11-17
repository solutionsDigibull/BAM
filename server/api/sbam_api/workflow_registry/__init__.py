"""The Workflow registry (ADR 0028, decision 4; issue WG-12, #760).

Register a certified, frozen Workflow server in Bifrost, supersede the older version, and look a
Workflow up by id and version.
"""

from sbam_api.workflow_registry.router import router

__all__ = ["router"]
