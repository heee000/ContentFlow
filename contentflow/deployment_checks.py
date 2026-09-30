"""Read-only, candidate-specific container checks used before promotion."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import socket
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import db
from .entities import WorkerNode
from .settings import Settings


def api_is_ready(payload: object, release_sha: str) -> bool:
    return isinstance(payload, dict) and all(payload.get(key) == value for key, value in {
        "status": "ready", "database": "ok", "schema": "ok", "storage": "ok", "release_sha": release_sha,
    }.items())


def worker_is_ready(session_factory, *, hostname: str, release_sha: str, stale_seconds: int) -> bool:
    # Running inside the candidate Worker container binds this query to its
    # hostname, not any recent heartbeat left by an old container/process.
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        return session.scalar(select(WorkerNode.id).where(
            WorkerNode.hostname == hostname,
            WorkerNode.status == "online",
            WorkerNode.stopped_at.is_(None),
            WorkerNode.heartbeat_at > now - timedelta(seconds=stale_seconds),
            WorkerNode.heartbeat_at <= now + timedelta(seconds=5),
            WorkerNode.metadata_json["release_sha"].as_string() == release_sha,
        ).limit(1)) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("api", "worker"))
    parser.add_argument("release_sha")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.release_sha):
        parser.error("release_sha must be 40 lowercase hexadecimal characters")
    engine = None
    try:
        if args.component == "api":
            with urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5) as response:
                raw = response.read(65537)
                ready = len(raw) <= 65536 and api_is_ready(json.loads(raw), args.release_sha)
        else:
            settings = Settings(_env_file=None)
            settings.validate_runtime()
            if settings.release_sha != args.release_sha:
                return 1
            engine = db.build_engine(settings.database_url)
            ready = worker_is_ready(sessionmaker(bind=engine), hostname=socket.gethostname(),
                release_sha=args.release_sha, stale_seconds=settings.worker_stale_seconds)
        return 0 if ready else 1
    except Exception:
        # Config, database and HTTP exception text can contain private values.
        print("Candidate readiness check failed; release not verified.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
