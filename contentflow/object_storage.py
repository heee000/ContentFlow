from __future__ import annotations

import hashlib
import mimetypes
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .settings import Settings


@dataclass(slots=True)
class StoredObject:
    uri: str
    checksum: str
    size_bytes: int
    mime_type: str


class ObjectStorage(Protocol):
    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream: BinaryIO,
        content_type: str | None = None,
    ) -> StoredObject: ...

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes: ...

    def check(self) -> None: ...


def safe_filename(filename: str) -> str:
    name = Path(filename).name.replace("\x00", "")
    if not name or name in {".", ".."}:
        raise ValueError("文件名无效")
    return name


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
        final = target_dir / f"{digest.hexdigest()[:16]}-{clean_name}"
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
            key = f"{workspace_id}/{category}/{checksum[:16]}-{clean_name}"
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
                    character in "0123456789abcdef"
                    for character in expected_prefix
                )
                and checksum.startswith(expected_prefix)
            )
        if not valid_checksum:
            raise ValueError("S3 对象完整性校验失败")
        return data

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
