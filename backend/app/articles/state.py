"""Article workflow state machine.

draft -> in_review -> approved -> (scheduled ->) published -> unpublished
Every state can be archived; archived articles can be restored to draft.
Publishing always needs an approved article unless the admin's auto-publish policy is on.
"""

from __future__ import annotations

from app.security import permissions as P

STATES = ("draft", "in_review", "approved", "scheduled", "published", "unpublished", "archived")

TRANSITIONS: dict[str, dict[str, str]] = {
    # from -> {to: required permission}
    "draft": {"in_review": P.ARTICLES_WRITE, "approved": P.ARTICLES_APPROVE, "archived": P.ARTICLES_WRITE},
    "in_review": {"draft": P.ARTICLES_WRITE, "approved": P.ARTICLES_APPROVE, "archived": P.ARTICLES_WRITE},
    "approved": {"draft": P.ARTICLES_WRITE, "scheduled": P.ARTICLES_PUBLISH, "published": P.ARTICLES_PUBLISH,
                 "archived": P.ARTICLES_WRITE},
    "scheduled": {"approved": P.ARTICLES_PUBLISH, "published": P.ARTICLES_PUBLISH, "draft": P.ARTICLES_WRITE,
                  "archived": P.ARTICLES_WRITE},
    "published": {"unpublished": P.ARTICLES_PUBLISH, "published": P.ARTICLES_PUBLISH},
    "unpublished": {"draft": P.ARTICLES_WRITE, "published": P.ARTICLES_PUBLISH, "archived": P.ARTICLES_WRITE},
    "archived": {"draft": P.ARTICLES_WRITE},
}


class TransitionError(Exception):
    pass


def check_transition(current: str, target: str, permissions: set[str]) -> None:
    allowed = TRANSITIONS.get(current, {})
    if target not in allowed:
        raise TransitionError(f"Cannot move an article from {current} to {target}")
    needed = allowed[target]
    if needed not in permissions:
        raise TransitionError(f"Moving from {current} to {target} requires {needed}")


def can_transition(current: str, target: str, permissions: set[str]) -> bool:
    try:
        check_transition(current, target, permissions)
        return True
    except TransitionError:
        return False


def available_transitions(current: str, permissions: set[str]) -> list[str]:
    return [t for t, perm in TRANSITIONS.get(current, {}).items() if perm in permissions and t != current]


EDIT_RESETS_APPROVAL = {"approved", "scheduled"}
