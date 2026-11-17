"""`registry.load_all` imports the activity modules, never the parse subprocess entry point."""

import sys

from sbam_workers import registry


def test_load_all_leaves_the_isolated_child_alone() -> None:
    """The child imports POSIX-only `resource` and stubs `sbam_workers.ingest.loaders` in
    `sys.modules`; importing it in the worker breaks Windows runs and can hide the loaders."""
    registry.load_all()
    assert "sbam_workers.ingest.isolated_child" not in sys.modules
    assert hasattr(sys.modules["sbam_workers.ingest.loaders"], "__file__")  # the real package
