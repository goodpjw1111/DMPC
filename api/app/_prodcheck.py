"""Pure prod-config validation (no pydantic) so it is unit-testable standalone."""

from __future__ import annotations

DEV_SECRET = "dev-only-change-me"


def prod_config_errors(
    *, app_env: str, secret_key: str, cookie_secure: bool, cookie_samesite: str,
    google_client_id: str, google_client_secret: str, csp_report_only: bool,
    web_origin: str, api_base_url: str,
) -> list[str]:
    """Reasons a production deployment must REFUSE to boot. Empty list = OK.
    Only enforced when app_env == 'prod'."""
    if app_env != "prod":
        return []
    errs: list[str] = []
    if secret_key in ("", DEV_SECRET) or len(secret_key) < 32:
        errs.append("SECRET_KEY must be a unique 32+ char value")
    if not cookie_secure:
        errs.append("COOKIE_SECURE must be true in prod (https)")
    if cookie_samesite.lower() == "none" and not cookie_secure:
        errs.append("SameSite=None requires Secure cookies")
    if not google_client_id or not google_client_secret:
        errs.append("GOOGLE_CLIENT_ID/SECRET must be set")
    if csp_report_only:
        errs.append("CSP_REPORT_ONLY should be false (enforced) in prod")
    if "localhost" in web_origin or "localhost" in api_base_url:
        errs.append("WEB_ORIGIN/API_BASE_URL still point at localhost")
    return errs
