"""Runtime configuration — all secrets come from the environment, never code.

A production deployment FAILS TO BOOT if security-critical settings are left at
their dev defaults (see `prod_config_errors`), so a misconfigured prod can't
silently ship with a known secret or non-Secure cookies.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._prodcheck import DEV_SECRET, prod_config_errors


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- environment ------------------------------------------------------
    app_env: str = "dev"                       # dev | prod
    api_base_url: str = "http://localhost:8000"
    # Comma-separated list of allowed browser origins (first = canonical).
    web_origin: str = "http://localhost:3000"

    # --- the one allowed domain ------------------------------------------
    allowed_email_domain: str = "dimigo.hs.kr"
    # Comma-separated EXACT emails allowed to log in even though they are NOT in the
    # domain above (operator exceptions). The OIDC layer skips the domain/hd gate for
    # these, but still requires a Google-verified email. Override via ALLOW_EMAILS.
    allow_emails: str = "goodpjw1111@gmail.com"

    # --- bootstrap admins -------------------------------------------------
    # Comma-separated emails auto-granted the 'admin' role on login (idempotent;
    # promotes an existing account, never demotes). Lets the first operator
    # self-provision admin without a manual SQL step. Override via ADMIN_EMAILS.
    admin_emails: str = "goodpjw2008@dimigo.hs.kr,goodpjw1111@gmail.com"

    # --- Google OIDC ------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"

    # --- sessions / cookies ----------------------------------------------
    secret_key: str = DEV_SECRET               # signs the short-lived oauth tx cookie
    ip_hash_secret: str = ""                   # separate pepper for IP hashing (falls back to secret_key)
    session_ttl_hours: int = 12
    cookie_secure: bool = False                # MUST be True in prod (https)
    cookie_samesite: str = "lax"               # lax (single-origin) | none (cross-origin) | strict
    session_cookie_name: str = "dmpc_session"
    csrf_cookie_name: str = "dmpc_csrf"
    oauth_tx_cookie_name: str = "dmpc_oauth_tx"

    # --- client IP behind a proxy ----------------------------------------
    # The trusted client-IP header for your host (e.g. "cf-connecting-ip" behind
    # Cloudflare). Empty -> use the socket peer (request.client.host). Raw
    # X-Forwarded-For is NEVER trusted (it is client-spoofable).
    trusted_ip_header: str = ""

    # --- data -------------------------------------------------------------
    database_url: str = "postgresql://dmpc:dmpc@localhost:5432/dmpc"

    # --- security headers -------------------------------------------------
    csp_report_only: bool = True

    # ---------------------------------------------------------------------
    @model_validator(mode="after")
    def _enforce_prod(self):
        errs = prod_config_errors(
            app_env=self.app_env, secret_key=self.secret_key,
            cookie_secure=self.cookie_secure, cookie_samesite=self.cookie_samesite,
            google_client_id=self.google_client_id,
            google_client_secret=self.google_client_secret,
            csp_report_only=self.csp_report_only, web_origin=self.web_origin,
            api_base_url=self.api_base_url,
        )
        if errs:
            raise ValueError("insecure prod config: " + "; ".join(errs))
        return self

    # --- derived ----------------------------------------------------------
    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def allow_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allow_emails.split(",") if e.strip()}

    @property
    def ip_pepper(self) -> str:
        return self.ip_hash_secret or self.secret_key

    def _prefixed(self, name: str, *, path_root: bool) -> str:
        # __Host- needs Secure + Path=/ + no Domain; __Secure- only needs Secure.
        if not self.cookie_secure or name.startswith(("__Host-", "__Secure-")):
            return name
        return ("__Host-" if path_root else "__Secure-") + name

    @property
    def effective_session_cookie(self) -> str:
        return self._prefixed(self.session_cookie_name, path_root=True)

    @property
    def effective_csrf_cookie(self) -> str:
        return self._prefixed(self.csrf_cookie_name, path_root=True)

    @property
    def effective_oauth_tx_cookie(self) -> str:
        # oauth tx cookie uses Path=/auth, so it can't be __Host- (needs Path=/).
        return self._prefixed(self.oauth_tx_cookie_name, path_root=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
