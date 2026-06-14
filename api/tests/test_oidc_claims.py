"""Security tests for the OIDC claim enforcement (pure stdlib, no network).

Run:  python api/tests/test_oidc_claims.py
This is the gate that keeps non-@dimigo.hs.kr users out, so it is tested
adversarially: every individual rule must independently reject.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from oidc import (  # noqa: E402
    AuthError,
    check_claims,
    code_challenge_s256,
    new_code_verifier,
)

DOMAIN = "dimigo.hs.kr"
NONCE = "the-expected-nonce"


def good_claims(**over):
    base = {
        "iss": "https://accounts.google.com",
        "aud": "client-123",
        "sub": "google-sub-123",
        "email": "student@dimigo.hs.kr",
        "email_verified": True,
        "hd": "dimigo.hs.kr",
        "nonce": NONCE,
    }
    base.update(over)
    return base


def expect_reject(claims, why):
    try:
        check_claims(claims, allowed_domain=DOMAIN, expected_nonce=NONCE)
    except AuthError:
        return
    raise AssertionError(f"should have rejected: {why}")


def test_valid_passes():
    check_claims(good_claims(), allowed_domain=DOMAIN, expected_nonce=NONCE)  # no raise


def test_alt_issuer_passes():
    check_claims(good_claims(iss="accounts.google.com"),
                 allowed_domain=DOMAIN, expected_nonce=NONCE)


def test_reject_unverified_email():
    expect_reject(good_claims(email_verified=False), "email_verified=False")


def test_reject_wrong_email_domain():
    expect_reject(good_claims(email="hacker@gmail.com", hd="dimigo.hs.kr"),
                  "email on gmail")


def test_reject_subdomain_lookalike():
    expect_reject(good_claims(email="x@evil.dimigo.hs.kr"), "subdomain look-alike")


def test_reject_missing_hd_even_if_email_ok():
    # Personal Gmail can carry a dimigo-looking email alias but no hd claim.
    expect_reject(good_claims(hd=None), "no hd claim")


def test_reject_wrong_hd():
    expect_reject(good_claims(hd="other.hs.kr"), "hd mismatch")


def test_reject_nonce_mismatch():
    expect_reject(good_claims(nonce="attacker-replayed"), "nonce replay")


def test_reject_missing_nonce():
    expect_reject(good_claims(nonce=None), "no nonce")


def test_reject_bad_issuer():
    expect_reject(good_claims(iss="https://evil.example.com"), "spoofed issuer")


def test_reject_missing_sub():
    expect_reject(good_claims(sub=None), "no immutable subject")


def test_case_insensitive_domain():
    check_claims(good_claims(email="Student@Dimigo.HS.KR", hd="Dimigo.HS.KR"),
                 allowed_domain=DOMAIN, expected_nonce=NONCE)


# --- allowlist exceptions (operator gmail etc.) ---------------------------
ALLOW = frozenset({"goodpjw1111@gmail.com"})


def test_allowlisted_email_passes_without_domain_or_hd():
    # exact allowlist exception: a personal gmail (no hd) is permitted.
    check_claims(good_claims(email="goodpjw1111@gmail.com", hd=None),
                 allowed_domain=DOMAIN, expected_nonce=NONCE, allow_emails=ALLOW)
    # case-insensitive match against the allowlist
    check_claims(good_claims(email="GoodPJW1111@Gmail.com", hd=None),
                 allowed_domain=DOMAIN, expected_nonce=NONCE, allow_emails=ALLOW)


def test_non_allowlisted_gmail_still_rejected():
    try:
        check_claims(good_claims(email="someoneelse@gmail.com", hd=None),
                     allowed_domain=DOMAIN, expected_nonce=NONCE, allow_emails=ALLOW)
    except AuthError:
        return
    raise AssertionError("a non-allowlisted gmail must still be rejected")


def test_allowlist_still_requires_verified_email_and_nonce():
    # the exception skips domain/hd ONLY — verification + nonce stay enforced.
    for bad in (dict(email_verified=False), dict(nonce="replayed")):
        try:
            check_claims(good_claims(email="goodpjw1111@gmail.com", hd=None, **bad),
                         allowed_domain=DOMAIN, expected_nonce=NONCE, allow_emails=ALLOW)
        except AuthError:
            continue
        raise AssertionError(f"allowlisted email must still enforce {list(bad)}")


def test_pkce_challenge_is_deterministic_b64url():
    v = new_code_verifier()
    c1 = code_challenge_s256(v)
    c2 = code_challenge_s256(v)
    assert c1 == c2
    assert "=" not in c1 and "+" not in c1 and "/" not in c1  # base64url, unpadded


if __name__ == "__main__":
    tests = [
        (n, o) for n, o in sorted(globals().items())
        if n.startswith("test_") and isinstance(o, types.FunctionType)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
