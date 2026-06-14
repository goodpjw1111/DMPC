"""Tests for the prod boot guard (pure, stdlib-only)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from _prodcheck import prod_config_errors  # noqa: E402

SECURE = dict(
    app_env="prod", secret_key="x" * 40, cookie_secure=True, cookie_samesite="lax",
    google_client_id="cid", google_client_secret="csecret", csp_report_only=False,
    web_origin="https://dmpc.example", api_base_url="https://api.dmpc.example",
)


def test_dev_env_never_errors():
    assert prod_config_errors(**{**SECURE, "app_env": "dev",
                                 "secret_key": "dev-only-change-me",
                                 "cookie_secure": False}) == []


def test_fully_configured_prod_passes():
    assert prod_config_errors(**SECURE) == []


def test_default_secret_blocks_prod():
    assert any("SECRET_KEY" in e for e in
               prod_config_errors(**{**SECURE, "secret_key": "dev-only-change-me"}))


def test_short_secret_blocks_prod():
    assert any("SECRET_KEY" in e for e in
               prod_config_errors(**{**SECURE, "secret_key": "short"}))


def test_insecure_cookie_blocks_prod():
    assert any("COOKIE_SECURE" in e for e in
               prod_config_errors(**{**SECURE, "cookie_secure": False}))


def test_samesite_none_requires_secure():
    errs = prod_config_errors(**{**SECURE, "cookie_samesite": "none", "cookie_secure": False})
    assert any("SameSite=None" in e or "COOKIE_SECURE" in e for e in errs)


def test_missing_oauth_blocks_prod():
    assert any("GOOGLE" in e for e in
               prod_config_errors(**{**SECURE, "google_client_secret": ""}))


def test_report_only_csp_blocks_prod():
    assert any("CSP" in e for e in
               prod_config_errors(**{**SECURE, "csp_report_only": True}))


def test_localhost_origin_blocks_prod():
    assert any("localhost" in e for e in
               prod_config_errors(**{**SECURE, "web_origin": "http://localhost:3000"}))


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
