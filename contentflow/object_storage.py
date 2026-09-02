from __future__ import annotations

import hashlib
import heapq
import mimetypes
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

from .filenames import safe_filename
from .settings import Settings


@dataclass(slots=True)
class StoredObject:
    uri: str
    checksum: str
    size_bytes: int
    mime_type: str


@dataclass(slots=True, frozen=True)
class StoredObjectMetadata:
    uri: str
    key: str
    size_bytes: int
    mime_type: str
    modified_at: datetime
    checksum: str | None = None


@dataclass(slots=True, frozen=True)
class StoredObjectPage:
    items: list[StoredObjectMetadata]
    next_cursor: str | None


def is_managed_storage_uri(settings: Settings, uri: object) -> bool:
    if not isinstance(uri, str):
        return False
    if uri.startswith("file://"):
        from .knowledge_service import local_path_from_uri

        try:
            path = local_path_from_uri(uri).resolve()
        except (OSError, ValueError):
            return False
        root = settings.local_storage_dir.resolve()
        return path != root and root in path.parents
    if uri.startswith("s3://"):
        return uri.startswith(f"s3://{settings.s3_bucket}/") and bool(
            uri.removeprefix(f"s3://{settings.s3_bucket}/")
        )
    return False


def is_workspace_storage_uri(
    settings: Settings,
    workspace_id: str,
    uri: object,
) -> bool:
    if (
        not workspace_id
        or "/" in workspace_id
        or "\\" in workspace_id
        or not isinstance(uri, str)
    ):
        return False
    if uri.startswith("file://"):
        from .knowledge_service import local_path_from_uri

        try:
            path = local_path_from_uri(uri).resolve()
        except (OSError, ValueError):
            return False
        workspace_root = (settings.local_storage_dir.resolve() / workspace_id).resolve()
        return path != workspace_root and workspace_root in path.parents
    if uri.startswith("s3://"):
        prefix = f"s3://{settings.s3_bucket}/{workspace_id}/"
        return uri.startswith(prefix) and bool(uri.removeprefix(prefix))
    return False


def _allocation_prefix(allocation_id: str | None) -> str:
    if allocation_id is None:
        return ""
    try:
        normalized = str(uuid.UUID(allocation_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("存储分配 ID 无效") from error
    return f"{normalized}-"


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _stored_object_name(
    clean_name: str,
    *,
    checksum_prefix: str,
    allocation_id: str | None,
) -> str:
    prefix = f"{_allocation_prefix(allocation_id)}{checksum_prefix}-"
    available = 255 - len(prefix.encode("utf-8"))
    suffix = Path(clean_name).suffix
    suffix_bytes = len(suffix.encode("utf-8"))
    if suffix and suffix_bytes < available:
        stem = clean_name[: -len(suffix)]
        stem = _truncate_utf8(stem, available - suffix_bytes).rstrip(" .")
        object_name = f"{stem or 'object'}{suffix}"
    else:
        object_name = _truncate_utf8(clean_name, available).rstrip(" .") or "object"
    return f"{prefix}{object_name}"


class ObjectStorage(Protocol):
    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream: BinaryIO,
        content_type: str | None = None,
        allocation_id: str | None = None,
    ) -> StoredObject: ...

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes: ...

    def delete(self, uri: str) -> None: ...

    def list_workspace_objects(
        self,
        workspace_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage: ...

    def workspace_uri_prefix(self, workspace_id: str) -> str: ...

    def check(self) -> None: ...


class LocalObjectStorage:
    def __init__(self, root: Path, *, max_upload_bytes: int):
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream: BinaryIO,
        content_type: str | None = None,
        allocation_id: str | None = None,
    ) -> StoredObject:
        clean_name = safe_filename(filename)
        target_dir = (self.root / workspace_id / category).resolve()
        if self.root not in target_dir.parents:
            raise ValueError("存储路径越界")
        target_dir.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        size = 0
        temporary = target_dir / f".{uuid.uuid4().hex}-{clean_name}.uploading"
        try:
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise ValueError(
                            f"Upload exceeds {self.max_upload_bytes} byte limit"
                        )
                    digest.update(chunk)
                    output.write(chunk)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        final = target_dir / _stored_object_name(
            clean_name,
            checksum_prefix=digest.hexdigest()[:16],
            allocation_id=allocation_id,
        )
        temporary.replace(final)
        mime = (
            content_type
            or mimetypes.guess_type(clean_name)[0]
            or "application/octet-stream"
        )
        return StoredObject(
            uri=final.as_uri(),
            checksum=digest.hexdigest(),
            size_bytes=size,
            mime_type=mime,
        )

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        from .knowledge_service import local_path_from_uri

        path = local_path_from_uri(uri).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("对象路径不属于当前存储根目录")
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size > max_bytes:
            raise ValueError("对象超过读取大小限制")
        return path.read_bytes()

    def delete(self, uri: str) -> None:
        from .knowledge_service import local_path_from_uri

        path = local_path_from_uri(uri).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("对象路径不属于当前存储根目录")
        path.unlink(missing_ok=True)

    def list_workspace_objects(
        self,
        workspace_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage:
        if not 1 <= limit <= 500:
            raise ValueError("对象巡检页长必须在 1 到 500 之间")
        workspace_root = (self.root / workspace_id).resolve()
        if self.root not in workspace_root.parents:
            raise ValueError("工作区存储路径越界")
        prefix = f"{workspace_id}/"
        if cursor is not None and not cursor.startswith(prefix):
            raise ValueError("对象巡检游标不属于当前工作区")
        if not workspace_root.exists():
            return StoredObjectPage(items=[], next_cursor=None)
        position = cursor or ""

        def candidates():
            for path in workspace_root.rglob("*"):
                if not path.is_file():
                    continue
                resolved_path = path.resolve()
                if self.root != resolved_path and self.root not in resolved_path.parents:
                    continue
                key = path.relative_to(self.root).as_posix()
                if key <= position:
                    continue
                stat = path.stat()
                yield StoredObjectMetadata(
                    uri=resolved_path.as_uri(),
                    key=key,
                    size_bytes=stat.st_size,
                    mime_type=(
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream"
                    ),
                    modified_at=datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=timezone.utc,
                    ),
                )

        selected = heapq.nsmallest(
            limit + 1,
            candidates(),
            key=lambda item: item.key,
        )
        page = selected[:limit]
        next_cursor = page[-1].key if len(selected) > limit and page else None
        return StoredObjectPage(items=page, next_cursor=next_cursor)

    def workspace_uri_prefix(self, workspace_id: str) -> str:
        workspace_root = (self.root / workspace_id).resolve()
        if self.root not in workspace_root.parents:
            raise ValueError("工作区存储路径越界")
        return f"{workspace_root.as_uri().rstrip('/')}/"

    def check(self) -> None:
        if not self.root.is_dir():
            raise RuntimeError(f"Local storage directory is unavailable: {self.root}")


class S3ObjectStorage:
    def __init__(self, settings: Settings):
        try:
            import boto3
        except ImportError as error:
            raise RuntimeError("使用 S3 存储需要安装 contentflow[s3]") from error
        self.bucket = settings.s3_bucket
        self.max_upload_bytes = settings.max_upload_bytes
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream: BinaryIO,
        content_type: str | None = None,
        allocation_id: str | None = None,
    ) -> StoredObject:
        clean_name = safe_filename(filename)
        digest = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(
            max_size=min(self.max_upload_bytes, 8 * 1024 * 1024)
        ) as staging:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_upload_bytes:
                    raise ValueError(
                        f"Upload exceeds {self.max_upload_bytes} byte limit"
                    )
                digest.update(chunk)
                staging.write(chunk)
            checksum = digest.hexdigest()
            object_name = _stored_object_name(
                clean_name,
                checksum_prefix=checksum[:16],
                allocation_id=allocation_id,
            )
            key = f"{workspace_id}/{category}/{object_name}"
            mime = (
                content_type
                or mimetypes.guess_type(clean_name)[0]
                or "application/octet-stream"
            )
            staging.seek(0)
            self.client.upload_fileobj(
                staging,
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": mime,
                    "Metadata": {"sha256": checksum},
                },
            )
        return StoredObject(
            uri=f"s3://{self.bucket}/{key}",
            checksum=checksum,
            size_bytes=size,
            mime_type=mime,
        )

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("S3 对象不属于当前 bucket")
        key = uri[len(prefix) :]
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        raw_content_length = response.get("ContentLength")
        content_length = (
            int(raw_content_length) if raw_content_length is not None else None
        )
        body = response["Body"]
        try:
            if content_length is not None and content_length > max_bytes:
                raise ValueError("对象超过读取大小限制")
            data = body.read(max_bytes + 1)
        finally:
            body.close()
        if len(data) > max_bytes:
            raise ValueError("对象超过读取大小限制")
        if content_length is not None and len(data) != content_length:
            raise OSError("S3 对象读取不完整")

        checksum = hashlib.sha256(data).hexdigest()
        metadata = dict(response.get("Metadata") or {})
        expected_checksum = metadata.get("sha256")
        if expected_checksum:
            valid_checksum = checksum == expected_checksum
        else:
            # Objects created before checksum metadata was introduced still
            # carry the first 16 checksum characters in their generated key.
            expected_prefix = key.rsplit("/", 1)[-1].split("-", 1)[0]
            valid_checksum = (
                len(expected_prefix) == 16
                and all(
                    character in "0123456789abcdef" for character in expected_prefix
                )
                and checksum.startswith(expected_prefix)
            )
        if not valid_checksum:
            raise ValueError("S3 对象完整性校验失败")
        return data

    def delete(self, uri: str) -> None:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("S3 对象不属于当前 bucket")
        key = uri[len(prefix) :]
        if not key:
            raise ValueError("S3 对象键不能为空")
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list_workspace_objects(
        self,
        workspace_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage:
        if not 1 <= limit <= 500:
            raise ValueError("对象巡检页长必须在 1 到 500 之间")
        prefix = f"{workspace_id}/"
        if cursor is not None and not cursor.startswith(prefix):
            raise ValueError("对象巡检游标不属于当前工作区")
        request = {
            "Bucket": self.bucket,
            "Prefix": prefix,
            "MaxKeys": limit + 1,
        }
        if cursor:
            request["StartAfter"] = cursor
        response = self.client.list_objects_v2(**request)
        selected = list(response.get("Contents") or [])
        page = selected[:limit]
        items = [
            StoredObjectMetadata(
                uri=f"s3://{self.bucket}/{item['Key']}",
                key=str(item["Key"]),
                size_bytes=int(item.get("Size") or 0),
                mime_type=(
                    mimetypes.guess_type(str(item["Key"]))[0]
                    or "application/octet-stream"
                ),
                modified_at=(
                    item["LastModified"]
                    if item.get("LastModified") is not None
                    else datetime.now(timezone.utc)
                ),
            )
            for item in page
        ]
        next_cursor = items[-1].key if len(selected) > limit and items else None
        return StoredObjectPage(items=items, next_cursor=next_cursor)

    def workspace_uri_prefix(self, workspace_id: str) -> str:
        if not workspace_id or "/" in workspace_id or "\\" in workspace_id:
            raise ValueError("工作区存储前缀无效")
        return f"s3://{self.bucket}/{workspace_id}/"

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


def build_object_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "local":
        return LocalObjectStorage(
            settings.local_storage_dir,
            max_upload_bytes=settings.max_upload_bytes,
        )
    if settings.storage_backend == "s3":
        return S3ObjectStorage(settings)
    raise ValueError(f"不支持的存储后端: {settings.storage_backend}")


def build_object_storage_for_uri(settings: Settings, uri: str) -> ObjectStorage:
    if uri.startswith("file://"):
        return LocalObjectStorage(
            settings.local_storage_dir,
            max_upload_bytes=settings.max_upload_bytes,
        )
    if uri.startswith("s3://"):
        return S3ObjectStorage(settings)
    raise ValueError("对象 URI 不属于受管存储")
