"""Error types of the code gates that the build workflow reacts to (no heavy imports: the workflow
imports this module)."""

from typing import Final

# The stored bundle no longer matches what `assemble` recorded (storage or hash mismatch): an
# integrity incident, never a verdict on the code. Non-retryable.
BUNDLE_TAMPERED: Final = "BUNDLE_TAMPERED"
# The gate was handed inputs that do not belong to the code attempt (no attempt row, not assembled,
# another template or manifest, a verdict row about another bundle): a wiring defect. Non-retryable.
GATE_INPUT_MISMATCH: Final = "AST_GATE_INPUT_MISMATCH"
