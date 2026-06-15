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


async def fire(event_type: str) -> None:
    """POST a repository_dispatch event (e.g. 'grade-samples' | 'evals'). Silent no-op
    when unconfigured; warns (never raises) on failure."""
    s = get_settings()
    token, repo = s.github_dispatch_token, s.github_repo
    if not token or not repo:
        return
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
        if r.status_code != 204:
            log.warning("repository_dispatch %s -> %s %s", event_type, r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001 — dispatch is best-effort
        log.warning("repository_dispatch %s failed: %s", event_type, e)
