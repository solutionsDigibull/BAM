"""#656: the local API docs sign in through Keycloak (PKCE) and never ship to a deployed env."""

from pathlib import Path

from fastapi.testclient import TestClient
from sbam_api.core.api_docs import SCHEME, SWAGGER_CLIENT_ID
from sbam_api.main import create_app
from test_core_app import ALL_OK, DEPLOYED, make_settings

ISSUER = "http://localhost:8080/realms/bullm"
REDIRECT = "/api/v1/docs/oauth2-redirect"


def client(tmp_path: Path, **settings: object) -> TestClient:
    return TestClient(
        create_app(make_settings(tmp_path, keycloak_issuer=ISSUER, **settings), ALL_OK)
    )


def test_e2_2c_t01_openapi_declares_the_keycloak_scheme_for_every_operation(
    tmp_path: Path,
) -> None:
    schema = client(tmp_path).get("/api/v1/openapi.json").json()

    flow = schema["components"]["securitySchemes"][SCHEME]["flows"]["authorizationCode"]
    assert schema["components"]["securitySchemes"][SCHEME]["type"] == "oauth2"
    assert flow["authorizationUrl"] == f"{ISSUER}/protocol/openid-connect/auth"
    assert flow["tokenUrl"] == f"{ISSUER}/protocol/openid-connect/token"
    assert schema["security"] == [{SCHEME: ["openid"]}]
    # the API's routes are still all there; the scheme only adds the button
    assert "/api/v1/me" in schema["paths"]


def test_e2_2c_t02_oauth2_redirect_page_is_served_under_the_api_base(tmp_path: Path) -> None:
    response = client(tmp_path).get(REDIRECT)

    assert response.status_code == 200
    assert "oauth2" in response.text.lower()


def test_e2_2c_t03_swagger_ui_signs_in_as_the_dev_client_with_pkce(tmp_path: Path) -> None:
    page = client(tmp_path).get("/api/v1/docs").text

    assert f'"clientId": "{SWAGGER_CLIENT_ID}"' in page
    assert '"usePkceWithAuthorizationCodeGrant": true' in page
    assert REDIRECT in page


def test_e2_2c_t04_a_deployed_env_publishes_no_docs_openapi_or_redirect(tmp_path: Path) -> None:
    deployed = client(tmp_path, **DEPLOYED)

    for path in ("/api/v1/docs", "/api/v1/openapi.json", REDIRECT, "/docs/oauth2-redirect"):
        assert deployed.get(path).status_code == 404, path
