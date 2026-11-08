"""Actor names from Cognito (docs/data-model.md, Actor names).

Alerts store only a user's `sub`; the API looks the name up when it returns the alert
and never returns the `sub`. Once the account is deleted (90 days after offboarding)
the name is "Former caregiver" (or "Former staff member" for a staff actor).

In the ops view only one actor name is looked up: the caregiver who acknowledged a
device problem. A nurse's acknowledge shows no name ("the care centre nurse"), ops show
"the ops team", and resolved or cancelled alerts (`resolved_by`, `cancelled_by`) are
never in the `open` index. `FORMER_STAFF` is here for the routes that return those.

Lookups use AdminGetUser with the `sub` as the username (the pool's username
attribute is email, so each user's username is their `sub`), and only the `name`
attribute is used. AdminGetUser can't list users, so the role can't enumerate the pool;
it does return the one user's other attributes, which are dropped at once. The `sub` is
format-checked first. Results (including "no such user") are cached per container for
5 minutes, as thresholds are: a rename or deletion shows within 5 minutes.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from typing import Any

FORMER_CAREGIVER = "Former caregiver"
FORMER_STAFF = "Former staff member"
# An account that exists but has no usable `name` (admins always set one).
UNNAMED_CAREGIVER = "Caregiver"

CACHE_TTL_S = 300.0
NAME_MAX_LEN = 100
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

# sub -> (the name shown, monotonic expiry)
_cache: dict[str, tuple[str, float]] = {}


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


def lookup(client: Any, user_pool_id: str, sub: str) -> tuple[bool, str | None]:
    """(account exists, its cleaned `name`). UserNotFoundException means the account
    is gone; any other AWS error is raised as it is."""
    try:
        result = client.admin_get_user(UserPoolId=user_pool_id, Username=sub)
    except Exception as exc:
        if _error_code(exc) == "UserNotFoundException":
            return False, None
        raise
    for attribute in result.get("UserAttributes") or []:
        if attribute.get("Name") == "name":
            return True, _clean(attribute.get("Value"))
    return True, None


def caregiver_names(
    client_factory: Callable[[], Any],
    user_pool_id: str,
    subs: Iterable[str],
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, str | None], int]:
    """({sub: display name, or None if the lookup failed}, failed lookups).

    A failed lookup (throttling, a network error) leaves the name None, so the portal
    shows "a caregiver", rather than failing the whole dashboard or claiming the
    account is gone. Failures are not cached. The client is created only when a lookup
    is needed.
    """
    names: dict[str, str | None] = {}
    failed = 0
    now = clock()
    for sub in sorted(set(subs)):
        if not SUB_RE.match(sub):
            continue
        cached = _cache.get(sub)
        if cached is not None and cached[1] > now:
            names[sub] = cached[0]
            continue
        try:
            exists, name = lookup(client_factory(), user_pool_id, sub)
        except Exception:  # noqa: BLE001 - degrade to "a caregiver", see above
            failed += 1
            names[sub] = None
            continue
        if not exists:
            shown = FORMER_CAREGIVER
        else:
            shown = name or UNNAMED_CAREGIVER
        _cache[sub] = (shown, now + CACHE_TTL_S)
        names[sub] = shown
    return names, failed
