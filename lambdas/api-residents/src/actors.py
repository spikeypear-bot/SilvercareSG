"""Actor names from Cognito (docs/data-model.md, Actor names). Adapted from
lambdas/api-dashboard/src/actors.py (each function is zipped from its own folder).

Alerts store only a `sub` for `acknowledged_by` (a caregiver's device-problem
acknowledge), `cancelled_by` (caregiver), and `resolved_by` (staff). The API returns the
user's Cognito `name`, never the `sub`; once the account is deleted it returns
"Former caregiver" or "Former staff member".

AdminGetUser with the `sub` as username (the pool's username attribute is email, so the
username is the `sub`): it can't list users; the user's other attributes are dropped at
once. Results are cached per container for 5 minutes. A failed lookup returns None (the
portal then shows no name) and isn't cached.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from typing import Any

FORMER = {"caregiver": "Former caregiver", "staff": "Former staff member"}
UNNAMED = {"caregiver": "Caregiver", "staff": "Staff member"}

CACHE_TTL_S = 300.0
NAME_MAX_LEN = 100
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

# sub -> (cleaned name, or "" for an account without one, or None for a deleted
# account; monotonic expiry)
_cache: dict[str, tuple[str | None, float]] = {}


def clear_cache() -> None:
    _cache.clear()


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > NAME_MAX_LEN or not value.isprintable():
        return None
    return value


def _error_code(exc: Exception) -> str | None:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return None


def lookup(client: Any, user_pool_id: str, sub: str) -> str | None:
    """The account's cleaned `name` ("" if it has none), or None if it was deleted.
    Any other AWS error is raised."""
    try:
        result = client.admin_get_user(UserPoolId=user_pool_id, Username=sub)
    except Exception as exc:
        if _error_code(exc) == "UserNotFoundException":
            return None
        raise
    for attribute in result.get("UserAttributes") or []:
        if attribute.get("Name") == "name":
            return _clean(attribute.get("Value")) or ""
    return ""


def names(
    client_factory: Callable[[], Any],
    user_pool_id: str,
    actors: Iterable[tuple[str, str]],
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[tuple[str, str], str | None], int]:
    """({(sub, kind): name to show, or None if the lookup failed}, failed lookups).
    `kind` is "caregiver" or "staff" and picks the fallback text."""
    out: dict[tuple[str, str], str | None] = {}
    failed = 0
    now = clock()
    for sub, kind in sorted(set(actors)):
        if not SUB_RE.match(sub) or kind not in FORMER:
            continue
        cached = _cache.get(sub)
        if cached is not None and cached[1] > now:
            name = cached[0]
        else:
            try:
                name = lookup(client_factory(), user_pool_id, sub)
            except Exception:  # noqa: BLE001 - degrade to no name, see above
                failed += 1
                out[(sub, kind)] = None
                continue
            _cache[sub] = (name, now + CACHE_TTL_S)
        if name is None:
            out[(sub, kind)] = FORMER[kind]
        else:
            out[(sub, kind)] = name or UNNAMED[kind]
    return out, failed
