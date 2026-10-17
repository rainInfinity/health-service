"""提供任务数据和模型制品的受保护本地文件存储。"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO
from uuid import UUID, uuid4

_CHUNK_SIZE = 64 * 1024


class StorageArea(StrEnum):
    """区分任务数据与模型制品的独立根目录。"""

    TASK_DATA = "task_data"  # 原始输入和任务处理输出。
    MODEL_ARTIFACT = "model_artifact"  # 已发布模型版本引用的制品。


@dataclass(frozen=True, slots=True)
class StoredFile:
    """返回完整写入文件的相对键和实际字节数。"""

    storage_key: str  # 相对于对应存储根目录的受控键。
    size_bytes: int  # 已完整写入的文件大小，单位字节。


class StorageError(Exception):
    """表示可安全映射给业务层的本地存储错误。"""


class InvalidStorageKey(StorageError):
    """表示存储键非法或试图逃逸根目录。"""


class StorageFileNotFound(StorageError):
    """表示授权存储键对应的文件不存在。"""


class EmptyUploadError(StorageError):
    """表示上传没有写入任何内容。"""


class UploadTooLargeError(StorageError):
    """表示上传内容超过配置大小上限。"""


class InvalidOutputFile(StorageError):
    """表示输出文件不属于本次执行目录或文件不可用。"""


class StorageConflict(StorageError):
    """表示唯一输出目录已存在，不能复用旧执行目录。"""


class LocalFileStorage:
    """管理两个本地根目录下的输入、制品和执行输出文件。"""

    def __init__(self, task_data_root: Path, model_artifact_root: Path) -> None:
        """创建并验证任务数据根目录与模型制品根目录。"""
        self.task_data_root = self._prepare_root(task_data_root)
        self.model_artifact_root = self._prepare_root(model_artifact_root)

    @staticmethod
    def _prepare_root(root: Path) -> Path:
        """创建可访问根目录并返回解析后的绝对路径。"""
        try:
            configured_root = Path(root)
            configured_root.mkdir(parents=True, exist_ok=True)
            resolved_root = configured_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise StorageError("文件存储目录不可用") from error
        if not resolved_root.is_dir() or not os.access(
            resolved_root,
            os.R_OK | os.W_OK | os.X_OK,
        ):
            raise StorageError("文件存储目录不可用")
        return resolved_root

    def _root_for(self, area: StorageArea) -> Path:
        """选择文件所属的任务数据或制品根目录。"""
        if area == StorageArea.TASK_DATA:
            return self.task_data_root
        if area == StorageArea.MODEL_ARTIFACT:
            return self.model_artifact_root
        raise StorageError("文件存储类型无效")

    @staticmethod
    def _safe_parts(storage_key: str) -> tuple[str, ...]:
        """拒绝绝对路径、非规范键、目录穿越和 Windows 路径。"""
        if not isinstance(storage_key, str) or not storage_key or "\\" in storage_key:
            raise InvalidStorageKey("文件存储键无效")
        posix_path = PurePosixPath(storage_key)
        if (
            posix_path.is_absolute()
            or PureWindowsPath(storage_key).is_absolute()
            or posix_path.as_posix() != storage_key
            or any(part in {"", ".", ".."} for part in posix_path.parts)
        ):
            raise InvalidStorageKey("文件存储键无效")
        return posix_path.parts

    @staticmethod
    def _contained_path(root: Path, path: Path, *, strict: bool) -> Path:
        """解析路径并拒绝越过根目录的符号链接或目录片段。"""
        try:
            resolved = path.resolve(strict=strict)
            resolved.relative_to(root)
        except FileNotFoundError as error:
            raise StorageFileNotFound("文件不存在") from error
        except (OSError, RuntimeError, ValueError) as error:
            raise InvalidStorageKey("文件存储键无效") from error
        return resolved

    def _storage_key(self, area: StorageArea, path: Path) -> str:
        """将根目录内的物理路径转为规范相对键。"""
        root = self._root_for(area)
        resolved = self._contained_path(root, path, strict=True)
        if not resolved.is_file():
            raise StorageFileNotFound("文件不存在")
        return resolved.relative_to(root).as_posix()

    def save_upload(
        self,
        source: BinaryIO | object,
        *,
        area: StorageArea,
        max_bytes: int,
    ) -> StoredFile:
        """分块保存上传内容；失败时只删除本次新建文件。"""
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise StorageError("上传大小配置无效")
        root = self._root_for(area)
        candidate = root / "uploads" / f"{uuid4().hex}.bin"
        created_path: Path | None = None
        size_bytes = 0
        try:
            target = self._contained_path(root, candidate, strict=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                created_path = target
                for chunk in self._chunks(source):
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise StorageError("上传数据格式无效")
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise UploadTooLargeError("上传文件超过大小上限")
                    if chunk:
                        destination.write(chunk)
            if size_bytes == 0:
                raise EmptyUploadError("不允许上传空文件")
            return StoredFile(
                storage_key=self._storage_key(area, target),
                size_bytes=size_bytes,
            )
        except StorageError:
            self._remove_partial_file(created_path)
            raise
        except Exception as error:
            self._remove_partial_file(created_path)
            raise StorageError("文件写入失败") from error
        except BaseException:
            self._remove_partial_file(created_path)
            raise

    @staticmethod
    def _chunks(source: BinaryIO | object) -> Iterator[bytes | bytearray | memoryview]:
        """从 Django 分块上传或文件流中逐块读取数据。"""
        chunk_reader = getattr(source, "chunks", None)
        if callable(chunk_reader):
            yield from chunk_reader(_CHUNK_SIZE)
            return

        read = getattr(source, "read", None)
        if not callable(read):
            raise StorageError("上传数据格式无效")
        while True:
            chunk = read(_CHUNK_SIZE)
            if not chunk:
                return
            yield chunk

    @staticmethod
    def _remove_partial_file(path: Path | None) -> None:
        """清除本次失败写入的文件，不触碰已有业务文件。"""
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def resolve_file(self, area: StorageArea, storage_key: str) -> Path:
        """解析已登记相对键并确保文件存在且位于对应根目录。"""
        root = self._root_for(area)
        parts = self._safe_parts(storage_key)
        resolved = self._contained_path(root, root.joinpath(*parts), strict=True)
        if not resolved.is_file():
            raise StorageFileNotFound("文件不存在")
        return resolved

    def delete_file(self, area: StorageArea, storage_key: str) -> None:
        """删除已校验的本地文件，供数据库登记失败时回滚本次写入。"""
        path = self.resolve_file(area, storage_key)
        try:
            path.unlink()
        except OSError as error:
            raise StorageError("文件清理失败") from error

    def create_execution_output_dir(self, task_id: int, execution_token: UUID) -> Path:
        """创建当前任务与执行令牌专属的输出目录。"""
        if (
            isinstance(task_id, bool)
            or not isinstance(task_id, int)
            or task_id <= 0
            or not isinstance(execution_token, UUID)
        ):
            raise InvalidOutputFile("执行输出目录无效")
        root = self.task_data_root
        candidate = root / "outputs" / str(task_id) / execution_token.hex
        try:
            self._contained_path(root, candidate.parent, strict=False)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            directory = self._contained_path(root, candidate, strict=False)
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise StorageConflict("执行输出目录已存在") from error
        except StorageError as error:
            raise InvalidOutputFile("执行输出目录无效") from error
        except OSError as error:
            raise StorageError("执行输出目录不可用") from error
        if not directory.is_dir():
            raise InvalidOutputFile("执行输出目录无效")
        return directory

    def validate_output_file(
        self,
        task_id: int,
        execution_token: UUID,
        output_path: Path,
    ) -> str:
        """仅将当前执行目录内已存在的普通文件转为结果存储键。"""
        if (
            isinstance(task_id, bool)
            or not isinstance(task_id, int)
            or task_id <= 0
            or not isinstance(execution_token, UUID)
        ):
            raise InvalidOutputFile("执行输出文件无效")
        root = self.task_data_root
        execution_dir = root / "outputs" / str(task_id) / execution_token.hex
        try:
            resolved_dir = self._contained_path(root, execution_dir, strict=True)
            resolved_file = self._contained_path(root, Path(output_path), strict=True)
            resolved_file.relative_to(resolved_dir)
        except (StorageError, ValueError, TypeError) as error:
            raise InvalidOutputFile("执行输出文件无效") from error
        if not resolved_dir.is_dir() or not resolved_file.is_file():
            raise InvalidOutputFile("执行输出文件无效")
        return resolved_file.relative_to(root).as_posix()
