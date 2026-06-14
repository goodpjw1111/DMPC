"""
Contest scheduling rule (pure, stdlib — unit-testable, KST-correct).

Rule (owner):
  * Register a contest on day D  ->  it STARTS the next day at 09:00 KST
    and ENDS 3 days later at 09:00 KST (a 3-day contest).
  * Interim evaluations run every day at 09:00 and 18:00 KST within the window.
  * The LAST evaluation is the one AT the end time (09:00 KST on the final day);
    it is the FINAL test. When its grading completes, final ranks are revealed.

KST is a fixed +09:00 (no DST), so all of this is exact.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))


def contest_window(registered_on: date) -> tuple[datetime, datetime]:
    """(starts_at, ends_at) in KST for a contest registered on `registered_on`."""
    starts_at = datetime.combine(registered_on + timedelta(days=1), time(9, 0), KST)
    ends_at = starts_at + timedelta(days=3)
    return starts_at, ends_at


def eval_times(starts_at: datetime, ends_at: datetime) -> list[tuple[datetime, bool]]:
    """All evaluation moments in [starts_at, ends_at] at 09:00 & 18:00 KST.
    Returns (time, is_final); is_final is True only for the moment == ends_at
    (the final test that produces the published ranks)."""
    out: list[datetime] = []
    d = starts_at.astimezone(KST).date()
    end_d = ends_at.astimezone(KST).date()
    while d <= end_d:
        for hh in (9, 18):
            t = datetime.combine(d, time(hh, 0), KST)
            if starts_at <= t <= ends_at:
                out.append(t)
        d += timedelta(days=1)
    out = sorted(set(out))
    return [(t, t == ends_at) for t in out]


def final_eval_time(starts_at: datetime, ends_at: datetime) -> datetime:
    """The final evaluation moment (== ends_at)."""
    return ends_at
