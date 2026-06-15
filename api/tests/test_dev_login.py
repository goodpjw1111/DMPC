"""Dev-login email gate (the only pure-logic part; the route itself needs a DB).

Run:  python api/tests/test_dev_login.py
"""

from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))   # import app.*

from app.routers.auth import admin_role_for, dev_email_ok  # noqa: E402

DOMAIN = "dimigo.hs.kr"
ADMINS = {"admin@dimigo.hs.kr"}


def test_accepts_allowed_domain():
    assert dev_email_ok("a@dimigo.hs.kr", DOMAIN)
    assert dev_email_ok("  Tester@DIMIGO.HS.KR ", DOMAIN)   # trimmed + lowercased


def test_rejects_other_domain():
    assert not dev_email_ok("a@gmail.com", DOMAIN)
    assert not dev_email_ok("a@evil-dimigo.hs.kr.com", DOMAIN)


def test_rejects_malformed():
    assert not dev_email_ok("", DOMAIN)
    assert not dev_email_ok("nodomain", DOMAIN)        # no @


def test_empty_domain_allows_any_at_address():
    # if no domain is configured, any well-formed address passes the gate.
    assert dev_email_ok("a@anywhere.com", "")
    assert not dev_email_ok("noatsign", "")


def test_allowlist_exception_bypasses_domain():
    allow = {"operator@gmail.com"}
    assert dev_email_ok("operator@gmail.com", DOMAIN, allow)        # exception passes
    assert dev_email_ok("  Operator@Gmail.com ", DOMAIN, allow)     # trim + case
    assert not dev_email_ok("other@gmail.com", DOMAIN, allow)          # not on the list
    assert dev_email_ok("a@dimigo.hs.kr", DOMAIN, allow)               # domain still works


def test_admin_role_for_bootstrap_email():
    assert admin_role_for("admin@dimigo.hs.kr", ADMINS) == "admin"
    assert admin_role_for("  Admin@Dimigo.HS.KR ", ADMINS) == "admin"   # trim + case


def test_admin_role_for_non_admin_is_student():
    assert admin_role_for("someone@dimigo.hs.kr", ADMINS) == "student"
    assert admin_role_for("admin@dimigo.hs.kr", set()) == "student"     # empty list -> nobody


def test_admin_role_for_gmail_exception():
    # the allowlisted gmail can also be a bootstrap admin
    admins = {"admin@dimigo.hs.kr", "operator@gmail.com"}
    assert admin_role_for("operator@gmail.com", admins) == "admin"
    assert admin_role_for("  Operator@Gmail.com ", admins) == "admin"


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
