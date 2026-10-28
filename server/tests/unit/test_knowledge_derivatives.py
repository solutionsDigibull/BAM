"""#371: `derivatives_of` reads the keys the parse step records on a document version.

The build tests inject their own lookup, so the real one was never run: it read a column
(`derivative_keys`) that does not exist, every parsed document looked unparsed and every build
on the Compose stack ended in EmptyCorpus."""

import uuid

from sbam_api.ingest.models import DocumentVersion
from sbam_workers.knowledge.derivatives import DOC_JSON, DOC_MD, Derivative, derivatives_of

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
SHA_MD, SHA_JSON = "a" * 64, "b" * 64


def key(sha: str, tenant: uuid.UUID = TENANT, project: uuid.UUID = PROJECT) -> str:
    return f"{tenant}/{project}/derived/{sha}"


def version(**fields: object) -> DocumentVersion:
    return DocumentVersion(tenant_id=TENANT, project_id=PROJECT, **fields)


def test_e5_371_a_parsed_version_has_its_doc_md_and_doc_json() -> None:
    found = derivatives_of(version(derived_md_key=key(SHA_MD), derived_json_key=key(SHA_JSON)))
    assert found == {
        DOC_MD: Derivative(storage_key=key(SHA_MD), sha256=SHA_MD),
        DOC_JSON: Derivative(storage_key=key(SHA_JSON), sha256=SHA_JSON),
    }


def test_e5_371_a_version_that_is_not_parsed_has_no_derivatives() -> None:
    assert derivatives_of(version()) == {}


def test_e5_371_only_the_keys_that_are_set_are_returned() -> None:
    assert list(derivatives_of(version(derived_md_key=key(SHA_MD)))) == [DOC_MD]


def test_e5_371_a_key_of_another_project_or_tenant_is_ignored() -> None:
    other_project = key(SHA_MD, project=uuid.uuid4())
    other_tenant = key(SHA_JSON, tenant=uuid.uuid4())
    assert (
        derivatives_of(version(derived_md_key=other_project, derived_json_key=other_tenant)) == {}
    )


def test_e5_371_a_key_that_is_not_a_derived_hash_is_ignored() -> None:
    uploads = f"{TENANT}/{PROJECT}/uploads/{SHA_MD}"
    short = f"{TENANT}/{PROJECT}/derived/{'a' * 63}"
    upper = f"{TENANT}/{PROJECT}/derived/{'A' * 64}"
    assert derivatives_of(version(derived_md_key=uploads, derived_json_key=short)) == {}
    assert derivatives_of(version(derived_md_key=upper)) == {}
