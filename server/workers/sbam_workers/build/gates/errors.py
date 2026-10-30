"""Error types of the code gates that the build workflow reacts to (no heavy imports: the workflow
imports this module)."""

from typing import Final

# The stored bundle no longer matches what `assemble` recorded (storage or hash mismatch): an
# integrity incident, never a verdict on the code. Non-retryable.
BUNDLE_TAMPERED: Final = "BUNDLE_TAMPERED"
# The gate was handed inputs that do not belong to the code attempt (no attempt row, not assembled,
# another template or manifest, a verdict row about another bundle): a wiring defect. Non-retryable.
GATE_INPUT_MISMATCH: Final = "AST_GATE_INPUT_MISMATCH"
# A static tool could not give a verdict (it timed out or crashed, clamd was unreachable, or its
# signatures are stale): retried by Temporal, never a correction. After the retries it is a platform
# defect (BUILD_ERROR, an operator fixes it).
STATIC_GATE_INFRA: Final = "STATIC_GATE_INFRA"
# The Promptfoo suite cannot be built from the stored test data (no dataset, or a case the suite
# refuses): a data defect no correction can fix. BUILD_ERROR for an operator (#526).
SUITE_INVALID: Final = "SuiteInvalid"
# The import gate could not start its container (no docker, the daemon or the pinned image is
# unavailable): retried by Temporal, never a correction and never a verdict on the code.
IMPORT_GATE_INFRA: Final = "IMPORT_GATE_INFRA"
