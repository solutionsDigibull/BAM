"""LLM provenance (E6.1): the `llm_calls` table, one row per model call attempt. The admin action
that invalidates cached answers (#421) is the package's router."""

from sbam_api.llm.router import router

__all__ = ["router"]
