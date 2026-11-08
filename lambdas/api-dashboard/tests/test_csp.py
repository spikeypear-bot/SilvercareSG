"""The frontend's content security policy (infra/frontend_hosting.tf) gains OneMap in
img-src only (docs/features/alerts-map.md, criterion 11). Kept with api-dashboard's
tests because that feature's backend is here; the check is text only."""

from __future__ import annotations

import re
from pathlib import Path

HOSTING_TF = Path(__file__).resolve().parents[3] / "infra" / "frontend_hosting.tf"
API = "${aws_apigatewayv2_api.api.api_endpoint}"
EXPECTED = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": "'self' data: https://www.onemap.gov.sg",
    "font-src": "'self'",
    "connect-src": f"'self' https://cognito-idp.ap-southeast-1.amazonaws.com {API}",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "object-src": "'none'",
}


def _directives() -> dict[str, str]:
    text = HOSTING_TF.read_text(encoding="utf-8")
    (policy,) = re.findall(r'content_security_policy\s*=\s*"([^"]*)"', text)
    pairs = [d.strip().split(" ", 1) for d in policy.split(";") if d.strip()]
    return {name: value for name, value in pairs}


def test_img_src_adds_only_onemap():
    assert _directives()["img-src"] == EXPECTED["img-src"]


def test_onemap_appears_in_no_other_directive():
    for name, value in _directives().items():
        if name != "img-src":
            assert "onemap" not in value, name


def test_every_other_directive_is_unchanged():
    assert _directives() == EXPECTED
