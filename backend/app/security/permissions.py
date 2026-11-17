"""Role-based access control: roles map to permission strings checked per route."""

from __future__ import annotations

READ = "read"
SOURCES_WRITE = "sources:write"
ASSIGNMENTS_WRITE = "assignments:write"
RESEARCH_WRITE = "research:write"
CLUSTERS_WRITE = "clusters:write"
IMAGES_WRITE = "images:write"
IMAGES_APPROVE = "images:approve"
ARTICLES_WRITE = "articles:write"
ARTICLES_APPROVE = "articles:approve"
ARTICLES_PUBLISH = "articles:publish"
PUBLISHING_MANAGE = "publishing:manage"
SETTINGS_WRITE = "settings:write"
USERS_MANAGE = "users:manage"
SYSTEM_MANAGE = "system:manage"

ALL_PERMISSIONS = [
    READ, SOURCES_WRITE, ASSIGNMENTS_WRITE, RESEARCH_WRITE, CLUSTERS_WRITE, IMAGES_WRITE,
    IMAGES_APPROVE, ARTICLES_WRITE, ARTICLES_APPROVE, ARTICLES_PUBLISH, PUBLISHING_MANAGE,
    SETTINGS_WRITE, USERS_MANAGE, SYSTEM_MANAGE,
]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(ALL_PERMISSIONS),
    "editor": {
        READ, SOURCES_WRITE, ASSIGNMENTS_WRITE, RESEARCH_WRITE, CLUSTERS_WRITE, IMAGES_WRITE,
        IMAGES_APPROVE, ARTICLES_WRITE, ARTICLES_APPROVE, ARTICLES_PUBLISH,
    },
    "researcher": {READ, ASSIGNMENTS_WRITE, RESEARCH_WRITE, CLUSTERS_WRITE, IMAGES_WRITE, ARTICLES_WRITE},
    "viewer": {READ},
}

ROLE_DESCRIPTIONS = {
    "admin": "Full access, including users, settings, publishing destinations and backups",
    "editor": "Manage sources, research, images and articles; approve and publish",
    "researcher": "Create assignments, review matches, collect images, draft articles",
    "viewer": "Read-only access",
}


def permissions_for(role_names: list[str] | set[str]) -> set[str]:
    perms: set[str] = set()
    for name in role_names:
        perms |= ROLE_PERMISSIONS.get(name, set())
    return perms
