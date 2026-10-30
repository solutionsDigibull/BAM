"""A-15 / F-20: the proxy keeps no access log, so a signed download URL never reaches one.

`GET /api/v1/files/{key}?exp=&sig=` carries its authorisation in the query string: a bearer token
for up to 15 minutes. Caddy writes an access log only when the Caddyfile has a `log` directive; this
guards that nobody adds one without deleting the query first. The run against the real image is
`test_a15_a_signed_url_is_not_in_the_proxy_log` in test_a08b_edge_rate_limit.py.
"""

import re
from pathlib import Path

CADDYFILE = Path(__file__).resolve().parents[3] / "compose/proxy/Caddyfile"


def directives(text: str) -> list[str]:
    """The first word of every non-comment line: Caddyfile directives and matchers."""
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [line.split()[0] for line in lines if line]


def test_a15_the_caddyfile_has_no_log_directive() -> None:
    assert "log" not in directives(
        CADDYFILE.read_text()
    ), "an access log would record the signed URL's query: delete `request>uri` query first"


def test_a15_the_check_sees_a_log_directive() -> None:
    sample = "portal.test {\n\tlog {\n\t\toutput stdout\n\t}\n}\n"
    assert "log" in directives(sample)
    assert "log" not in directives("# log is not enabled\nportal.test {\n\treverse_proxy x\n}\n")
    assert re.search(r"\blog\b", sample)
