"""Fire a GitHub repository_dispatch so a Challenge submission / eval round triggers
its grading workflow IMMEDIATELY, instead of waiting for the Actions cron.

Best-effort and OPTIONAL: a no-op unless GITHUB_DISPATCH_TOKEN + GITHUB_REPO are set,
and it never raises into the caller (a failed dispatch must not fail a submission).
The grade-samples / evals workflows listen via `on: repository_dispatch`.
"""

from __future__ import annotations

import logging

from .config import get_settings

log = logging.getLogger("dmpc.dispatch")


async def fire(event_type: str) -> str:
    """POST a repository_dispatch event (e.g. 'grade-samples' | 'evals') and report what
    happened so callers can tell the user instead of failing silently. Returns:
      'sent'         — GitHub accepted it (204); the workflow will run within seconds.
      'unconfigured' — no GITHUB_DISPATCH_TOKEN/GITHUB_REPO; grading falls back to the cron.
      'error'        — configured but the dispatch failed (logged). Never raises."""
    s = get_settings()
    token, repo = s.github_dispatch_token, s.github_repo
    if not token or not repo:
        return "unconfigured"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"https://api.github.com/repos/{repo}/dispatches",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"event_type": event_type},
            )
        if r.status_code == 204:
            return "sent"
        log.warning("repository_dispatch %s -> %s %s", event_type, r.status_code, r.text[:200])
        return "error"
    except Exception as e:  # noqa: BLE001 — dispatch is best-effort
        log.warning("repository_dispatch %s failed: %s", event_type, e)
        return "error"
