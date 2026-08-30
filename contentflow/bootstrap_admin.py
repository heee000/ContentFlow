from __future__ import annotations

import argparse
import getpass

from sqlalchemy import func, select, text

from . import db
from .audit import record_audit
from .entities import Membership, User, Workspace
from .migrate import upgrade_database
from .routers.auth import make_slug
from .schemas import RegisterRequest
from .security import hash_password
from .settings import Settings


def _lock_bootstrap(session, settings: Settings) -> None:
    if settings.database_url.startswith("postgresql"):
        session.execute(text("SELECT pg_advisory_xact_lock(434663277611381820)"))


def _validated_user(
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
) -> RegisterRequest:
    return RegisterRequest(
        email=email,
        password=password,
        display_name=display_name,
        workspace_name=workspace_name,
    )


def bootstrap_workspace_admin(
    settings: Settings,
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
) -> tuple[str, str]:
    if settings.allow_registration:
        raise RuntimeError("Offline bootstrap requires public registration to be disabled")
    payload = _validated_user(
        email=email,
        password=password,
        display_name=display_name,
        workspace_name=workspace_name,
    )
    with db.SessionLocal() as session:
        _lock_bootstrap(session, settings)
        if session.scalar(select(func.count(User.id))) or session.scalar(
            select(func.count(Workspace.id))
        ):
            raise RuntimeError("Offline workspace bootstrap is allowed only on an empty database")
        user = User(
            email=str(payload.email).lower(),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name.strip(),
        )
        session.add(user)
        session.flush()
        workspace = Workspace(
            name=payload.workspace_name.strip(),
            slug=make_slug(payload.workspace_name),
            created_by=user.id,
        )
        session.add(workspace)
        session.flush()
        session.add(
            Membership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="admin",
            )
        )
        record_audit(
            session,
            action="bootstrap.workspace_admin.create",
            entity_type="user",
            entity_id=user.id,
            workspace_id=workspace.id,
            actor_user_id=None,
            metadata={"method": "offline_cli"},
        )
        session.commit()
        return workspace.slug, user.id


def add_workspace_admin(
    settings: Settings,
    *,
    workspace_slug: str,
    email: str,
    password: str,
    display_name: str,
) -> str:
    if settings.allow_registration:
        raise RuntimeError("Offline bootstrap requires public registration to be disabled")
    payload = _validated_user(
        email=email,
        password=password,
        display_name=display_name,
        workspace_name="existing-workspace",
    )
    with db.SessionLocal() as session:
        _lock_bootstrap(session, settings)
        workspace = session.scalar(
            select(Workspace).where(Workspace.slug == workspace_slug)
        )
        if workspace is None:
            raise RuntimeError("Workspace slug was not found")
        email_value = str(payload.email).lower()
        if session.scalar(select(User).where(User.email == email_value)) is not None:
            raise RuntimeError("Offline add-admin refuses an existing user email")
        user = User(
            email=email_value,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name.strip(),
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="admin",
            )
        )
        record_audit(
            session,
            action="bootstrap.workspace_admin.add",
            entity_type="user",
            entity_id=user.id,
            workspace_id=workspace.id,
            actor_user_id=None,
            metadata={"method": "offline_cli"},
        )
        session.commit()
        return user.id


def _read_password() -> str:
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise RuntimeError("Password confirmation does not match")
    return password


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create public-test administrators without opening registration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-workspace")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--workspace-name", required=True)
    add_admin = subparsers.add_parser("add-admin")
    add_admin.add_argument("--workspace-slug", required=True)
    add_admin.add_argument("--email", required=True)
    add_admin.add_argument("--display-name", required=True)
    args = parser.parse_args()

    settings = Settings(_env_file=None)
    settings.validate_runtime()
    upgrade_database(settings)
    db.configure_database(settings.database_url)
    password = _read_password()
    if args.command == "bootstrap-workspace":
        workspace_slug, _user_id = bootstrap_workspace_admin(
            settings,
            email=args.email,
            password=password,
            display_name=args.display_name,
            workspace_name=args.workspace_name,
        )
        print(f"First administrator created; workspace slug: {workspace_slug}")
    else:
        add_workspace_admin(
            settings,
            workspace_slug=args.workspace_slug,
            email=args.email,
            password=password,
            display_name=args.display_name,
        )
        print("Additional workspace administrator created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
