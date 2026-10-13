"""Transition errors -> API errors (TD §14.8, §18).

| Failure                                    | Response                      |
|--------------------------------------------|-------------------------------|
| edit on a locked state (LOCKED)            | 423 RESOURCE_LOCKED           |
| any other invalid transition / actor       | 409 INVALID_TRANSITION        |
| stale expected_row_version                 | 412 PRECONDITION_FAILED       |
| row frozen by the DB trigger               | 423 RESOURCE_LOCKED           |
| missing / other tenant / other project     | 404 NOT_FOUND                 |
"""

from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import InvalidTransition, InvalidTransitionCode

from sbam_api.core.conventions import ApiError, locked


def transition_error(exc: InvalidTransition) -> ApiError:
    if exc.code is InvalidTransitionCode.LOCKED:
        return locked()
    return ApiError(409, ErrorCode.INVALID_TRANSITION, "Invalid transition", detail=exc.message)
