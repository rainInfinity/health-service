"""提供任务数据和模型制品的受保护本地文件存储。"""

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

_CHUNK_SIZE = 64 * 1024
_SAFE_PATH_SEGMENT_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._"
)
_SAFE_CHANNEL_SEGMENT_BYTES = _SAFE_PATH_SEGMENT_BYTES | {ord("-")}


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


class InvalidStorageName(StorageError):
    """表示路径元数据无效或编码后的名称超出文件系统限制。"""


class StorageFileNotFound(StorageError):
    """表示授权存储键对应的文件不存在。"""


class EmptyUploadError(StorageError):
    """表示上传没有写入任何内容。"""


class UploadTooLargeError(StorageError):
    """表示上传内容超过配置大小上限。"""


class InvalidUploadExtension(StorageError):
    """表示上传文件名后缀缺失或不符合算法格式契约。"""


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
    def _encode_path_segment(value: str, *, allow_hyphen: bool = False) -> str:
        """按 UTF-8 字节进行唯一可逆编码，避免元数据形成路径分隔符。"""
        if not isinstance(value, str) or not value:
            raise InvalidStorageName("文件存储名称无效")
        try:
            value_bytes = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidStorageName("文件存储名称无效") from error
        if value in {".", ".."}:
            return "%2E" * len(value)
        safe_bytes = _SAFE_CHANNEL_SEGMENT_BYTES if allow_hyphen else _SAFE_PATH_SEGMENT_BYTES
        return "".join(chr(byte) if byte in safe_bytes else f"%{byte:02X}" for byte in value_bytes)

    @staticmethod
    def _validate_component_lengths(root: Path, components: tuple[str, ...]) -> None:
        """按当前文件系统的名称上限拒绝过长路径片段，不截断元数据。"""
        try:
            name_max = os.pathconf(root, "PC_NAME_MAX")
        except (OSError, ValueError) as error:
            raise StorageError("文件存储目录不可用") from error
        if isinstance(name_max, bool) or not isinstance(name_max, int) or name_max <= 0:
            raise StorageError("文件存储目录不可用")
        for component in components:
            try:
                component_size = len(component.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise InvalidStorageName("文件存储名称无效") from error
            if (
                component in {"", ".", ".."}
                or "/" in component
                or "\\" in component
                or component_size > name_max
            ):
                raise InvalidStorageName("文件存储名称无效")

    @staticmethod
    def _task_date_parts(created_at: datetime) -> tuple[str, str]:
        """将带时区的任务创建时间一次转换为上海日期和文件名前缀。"""
        if not isinstance(created_at, datetime):
            raise InvalidStorageName("文件存储名称无效")
        try:
            if created_at.utcoffset() is None:
                raise InvalidStorageName("文件存储名称无效")
            local_created_at = created_at.astimezone(ZoneInfo("Asia/Shanghai"))
        except (KeyError, OverflowError, ValueError) as error:
            raise InvalidStorageName("文件存储名称无效") from error
        return (
            local_created_at.strftime("%Y%m%d"),
            local_created_at.strftime("%Y%m%d_%H%M%S"),
        )

    def save_task_input(
        self,
        source: BinaryIO | object,
        *,
        source_filename: str,
        allowed_extensions: frozenset[str],
        created_at: datetime,
        task_id: int,
        user_id: int,
        channel: str,
        algorithm_key: str,
        logical_name: str,
        max_bytes: int,
    ) -> StoredFile:
        """按任务创建时间、归属和算法契约保存具有真实后缀的输入。"""
        extension = self._validate_upload_extension(
            source_filename,
            allowed_extensions,
        )
        if (
            isinstance(task_id, bool)
            or not isinstance(task_id, int)
            or task_id <= 0
            or isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or not isinstance(created_at, datetime)
        ):
            raise InvalidStorageName("文件存储名称无效")
        date_part, timestamp_part = self._task_date_parts(created_at)
        channel_part = self._encode_path_segment(channel, allow_hyphen=True)
        algorithm_part = self._encode_path_segment(algorithm_key)
        logical_name_part = self._encode_path_segment(logical_name)
        filename = (
            f"{timestamp_part}-{task_id}-{user_id}-{algorithm_part}-"
            f"{logical_name_part}-{uuid4().hex}.{extension}"
        )
        components = (channel_part, date_part, filename)
        root = self.task_data_root
        self._validate_component_lengths(root, components)
        candidate = root.joinpath(*components)
        return self._save_upload(
            source,
            area=StorageArea.TASK_DATA,
            candidate=candidate,
            max_bytes=max_bytes,
        )

    def save_model_artifact(
        self,
        source: BinaryIO | object,
        *,
        source_filename: str,
        allowed_extensions: frozenset[str],
        model_code: str,
        version: str,
        logical_name: str,
        max_bytes: int,
    ) -> StoredFile:
        """按稳定模型编码、版本和逻辑名保存具有真实后缀的制品。"""
        extension = self._validate_upload_extension(
            source_filename,
            allowed_extensions,
        )
        components = (
            self._encode_path_segment(model_code),
            self._encode_path_segment(version),
            f"{self._encode_path_segment(logical_name)}-{uuid4().hex}.{extension}",
        )
        root = self.model_artifact_root
        self._validate_component_lengths(root, components)
        return self._save_upload(
            source,
            area=StorageArea.MODEL_ARTIFACT,
            candidate=root.joinpath(*components),
            max_bytes=max_bytes,
        )

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

    @staticmethod
    def _validate_upload_extension(
        source_filename: str,
        allowed_extensions: frozenset[str],
    ) -> str:
        """只提取并校验客户端文件名后缀，不使用其路径部分。"""
        if not isinstance(source_filename, str) or not source_filename:
            raise InvalidUploadExtension("上传文件格式无效")
        suffix = PureWindowsPath(source_filename).suffix
        extension = suffix[1:].lower() if suffix.startswith(".") else ""
        if not extension or extension not in allowed_extensions:
            raise InvalidUploadExtension("上传文件格式无效")
        return extension

    def _save_upload(
        self,
        source: BinaryIO | object,
        *,
        area: StorageArea,
        candidate: Path,
        max_bytes: int,
    ) -> StoredFile:
        """在根目录内独占分块写入，并在失败时删除本次文件。"""
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise StorageError("上传大小配置无效")
        root = self._root_for(area)
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

    def resolve_model_artifact_candidate(self, storage_key: str) -> Path:
        """仅供固定场景延迟加载制品；校验边界但不要求最终文件存在或为普通文件。"""
        root = self.model_artifact_root
        parts = self._safe_parts(storage_key)
        if not parts:
            raise InvalidStorageKey("文件存储键无效")
        resolved_root = self._contained_path(root, root, strict=True)
        if not resolved_root.is_dir():
            raise StorageError("文件存储目录不可用")
        return self._contained_path(root, root.joinpath(*parts), strict=False)

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
        candidate = root / ".executions" / str(task_id) / execution_token.hex
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
        execution_dir = root / ".executions" / str(task_id) / execution_token.hex
        try:
            resolved_dir = self._contained_path(root, execution_dir, strict=True)
            resolved_file = self._contained_path(root, Path(output_path), strict=True)
            resolved_file.relative_to(resolved_dir)
        except (StorageError, ValueError, TypeError) as error:
            raise InvalidOutputFile("执行输出文件无效") from error
        if not resolved_dir.is_dir() or not resolved_file.is_file():
            raise InvalidOutputFile("执行输出文件无效")
        return resolved_file.relative_to(root).as_posix()

    def publish_task_output_directory(
        self,
        *,
        channel: str,
        created_at: datetime,
        task_id: int,
        output_storage_keys: Mapping[str, str],
    ) -> bool:
        """原子切换正式结果链接；旧布局和空结果保持只读、不发布。"""
        if (
            isinstance(task_id, bool)
            or not isinstance(task_id, int)
            or task_id <= 0
            or not isinstance(output_storage_keys, Mapping)
        ):
            raise InvalidOutputFile("正式结果目录无效")
        if not output_storage_keys:
            return False

        task_id_part = str(task_id)
        execution_token: str | None = None
        storage_keys: list[str] = []
        for logical_name, storage_key in output_storage_keys.items():
            if (
                not isinstance(logical_name, str)
                or not logical_name
                or not isinstance(storage_key, str)
                or not storage_key
            ):
                raise InvalidOutputFile("正式结果目录无效")
            parts = self._safe_parts(storage_key)
            if parts[0] != ".executions":
                return False
            if len(parts) < 4 or parts[1] != task_id_part:
                raise InvalidOutputFile("正式结果目录无效")
            token = parts[2]
            if len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
                raise InvalidOutputFile("正式结果目录无效")
            try:
                if UUID(hex=token).hex != token:
                    raise InvalidOutputFile("正式结果目录无效")
            except ValueError as error:
                raise InvalidOutputFile("正式结果目录无效") from error
            if execution_token is not None and execution_token != token:
                raise InvalidOutputFile("正式结果目录无效")
            execution_token = token
            storage_keys.append(storage_key)

        if execution_token is None:
            return False
        root = self.task_data_root
        execution_candidate = root / ".executions" / task_id_part / execution_token
        temporary_path: Path | None = None
        temporary_created = False
        try:
            execution_dir = self._contained_path(
                root,
                execution_candidate,
                strict=True,
            )
            if execution_dir != execution_candidate or not execution_dir.is_dir():
                raise InvalidOutputFile("正式结果目录无效")
            for storage_key in storage_keys:
                output_file = self.resolve_file(StorageArea.TASK_DATA, storage_key)
                output_file.relative_to(execution_dir)

            date_part, _ = self._task_date_parts(created_at)
            channel_part = self._encode_path_segment(channel, allow_hyphen=True)
            output_parent = root / channel_part / date_part / "outputs"
            self._validate_component_lengths(
                root,
                (channel_part, date_part, "outputs", task_id_part),
            )
            resolved_parent = self._contained_path(
                root,
                output_parent,
                strict=False,
            )
            if resolved_parent != output_parent:
                raise InvalidStorageKey("正式结果目录无效")
            output_parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = self._contained_path(
                root,
                output_parent,
                strict=True,
            )
            if resolved_parent != output_parent:
                raise InvalidStorageKey("正式结果目录无效")

            destination = output_parent / task_id_part
            if destination.is_symlink():
                existing_target = os.readlink(destination)
                if Path(existing_target).is_absolute():
                    raise InvalidStorageKey("正式结果目录无效")
                resolved_target = self._contained_path(
                    root,
                    destination,
                    strict=False,
                )
                if resolved_target == execution_dir:
                    return True
            elif destination.exists():
                raise StorageConflict("正式结果目录已存在且不是受控链接")

            relative_target = os.path.relpath(execution_dir, output_parent)
            temporary_path = output_parent / f".{task_id_part}-{uuid4().hex}.tmp"
            self._validate_component_lengths(root, (temporary_path.name,))
            os.symlink(relative_target, temporary_path, target_is_directory=True)
            temporary_created = True
            os.replace(temporary_path, destination)
            temporary_created = False
            return True
        except StorageError:
            if temporary_created:
                self._remove_partial_file(temporary_path)
            raise
        except (OSError, TypeError, ValueError) as error:
            if temporary_created:
                self._remove_partial_file(temporary_path)
            raise StorageError("正式结果目录发布失败") from error
        except BaseException:
            if temporary_created:
                self._remove_partial_file(temporary_path)
            raise
