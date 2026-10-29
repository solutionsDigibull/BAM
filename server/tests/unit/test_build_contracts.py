"""#446 contract: what `correct` reports about the ISATVON (`CorrectionResult`). Pure models, no
Docker. Replay safety is the point: a result recorded as a bare `IdResult` must still decode."""

import uuid

import pytest
from pydantic import ValidationError
from sbam_contracts.build import MAX_CHANGED_PATHS, CorrectionResult, IsatvonPatchClass
from sbam_contracts.pipeline import IdResult
from temporalio.contrib.pydantic import pydantic_data_converter

ID = uuid.uuid4()


def test_e11_4_446_the_classes_are_none_neutral_and_behaviour() -> None:
    assert [c.value for c in IsatvonPatchClass] == ["NONE", "NEUTRAL", "BEHAVIOUR"]


def test_e11_4_446_a_result_without_a_patch_is_the_old_id_result_plus_defaults() -> None:
    result = CorrectionResult(id=ID)
    assert isinstance(result, IdResult)  # the workflow can treat it as one
    assert (result.isatvon_patch, result.changed_paths) == (IsatvonPatchClass.NONE, [])


def test_e11_4_446_a_result_recorded_as_an_id_result_still_decodes() -> None:
    """Histories recorded before this change hold `{"id": ...}`; replay decodes them with the
    activity's new return type."""
    converter = pydantic_data_converter.payload_converter
    recorded = converter.to_payload(IdResult(id=ID))
    decoded = converter.from_payload(recorded, CorrectionResult)
    assert decoded == CorrectionResult(id=ID)


def test_e11_4_446_a_result_with_a_patch_round_trips_through_temporals_converter() -> None:
    converter = pydantic_data_converter.payload_converter
    result = CorrectionResult(
        id=ID,
        isatvon_patch=IsatvonPatchClass.BEHAVIOUR,
        changed_paths=["/isatvon_definition/v_variables/max_iterations"],
    )
    assert converter.from_payload(converter.to_payload(result), CorrectionResult) == result


def test_e11_4_446_the_result_is_strict_and_frozen() -> None:
    with pytest.raises(ValidationError):
        CorrectionResult(id=ID, isatvon_patch="LOOSER")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CorrectionResult.model_validate({"id": str(ID), "diff": "x"})  # no free-form payload
    result = CorrectionResult(id=ID)
    with pytest.raises(ValidationError):
        result.isatvon_patch = IsatvonPatchClass.BEHAVIOUR


@pytest.mark.parametrize(
    "paths",
    [
        ["/p"] * (MAX_CHANGED_PATHS + 1),  # too many
        ["/" + "x" * 200],  # a path over 200 characters
        [""],  # an empty path
    ],
    ids=["too-many", "too-long", "empty"],
)
def test_e11_4_446_changed_paths_are_bounded(paths: list[str]) -> None:
    with pytest.raises(ValidationError):
        CorrectionResult(id=ID, changed_paths=paths)


def test_e11_4_446_the_limit_itself_is_allowed() -> None:
    paths = ["/" + "x" * 199] * MAX_CHANGED_PATHS
    assert len(CorrectionResult(id=ID, changed_paths=paths).changed_paths) == MAX_CHANGED_PATHS
