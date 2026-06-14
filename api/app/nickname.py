"""Nickname rules (pure, stdlib) — unit-testable without pydantic/DB.

Format (a common, conservative ASCII handle format):
  * 2–16 characters
  * allowed: ASCII letters, digits, underscore (NO Korean / non-ASCII)
  * must start and end with a letter/digit (no leading/trailing underscore)
  * no whitespace or other symbols
Uniqueness is case-insensitive and enforced by the DB (citext UNIQUE); the API
maps a unique-violation to a 409 with a friendly message.
"""

from __future__ import annotations

import re

MIN_LEN, MAX_LEN = 2, 16
_ALLOWED = re.compile(r"^[A-Za-z0-9_]+$")
RESERVED = {
    "admin", "administrator", "root", "system", "dmpc", "nypc",
    "null", "undefined", "anonymous", "me", "you",
}


def validate_nickname(name: str) -> str | None:
    """Return a Korean error string if invalid, else None."""
    if name is None:
        return "닉네임을 입력하세요."
    name = name.strip()
    if len(name) < MIN_LEN:
        return f"닉네임은 {MIN_LEN}자 이상이어야 합니다."
    if len(name) > MAX_LEN:
        return f"닉네임은 {MAX_LEN}자 이하여야 합니다."
    if not _ALLOWED.match(name):
        return "영문·숫자·밑줄(_)만 사용할 수 있습니다. (한글 불가)"
    if name[0] == "_" or name[-1] == "_":
        return "밑줄(_)로 시작하거나 끝날 수 없습니다."
    if name.lower() in RESERVED:
        return "사용할 수 없는 닉네임입니다."
    return None


def normalize(name: str) -> str:
    return name.strip()
