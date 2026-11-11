"""`GET /preflight`: can the platform do real work, before a build finds out the hard way."""

from sbam_api.preflight.router import router

__all__ = ["router"]
