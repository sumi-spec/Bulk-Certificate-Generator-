"""File Storage Abstraction Service."""

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


class StorageService:
    """Handles secure file storage and retrieval with path traversal defenses."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or settings.resolved_storage_path).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def resolve_safe_path(self, relative_path: str) -> Path:
        """
        Resolves a relative path against the base storage directory.
        Raises ValueError if a path traversal attempt is detected.
        """
        # Normalize and resolve target path
        target_path = (self.base_dir / relative_path).resolve()
        try:
            # Ensure target_path is within base_dir
            target_path.relative_to(self.base_dir)
        except ValueError as exc:
            logger.warning("Path traversal attempt detected: %s", relative_path)
            raise ValueError(f"Access denied: invalid file path '{relative_path}'") from exc

        return target_path

    def save_file(self, relative_path: str, content: bytes) -> tuple[str, int, str]:
        """
        Saves binary content to disk safely.
        Returns:
            tuple of (absolute_path_str, file_size_bytes, sha256_hex_digest)
        """
        safe_path = self.resolve_safe_path(relative_path)
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        with open(safe_path, "wb") as f:
            f.write(content)

        file_size = len(content)
        sha256_hash = hashlib.sha256(content).hexdigest()
        logger.debug("File saved at %s (%d bytes, sha256: %s)", safe_path, file_size, sha256_hash)
        return str(safe_path), file_size, sha256_hash

    def file_exists(self, relative_path: str) -> bool:
        """Checks if a file exists within safe storage bounds."""
        try:
            safe_path = self.resolve_safe_path(relative_path)
            return safe_path.is_file()
        except ValueError:
            return False

    def get_file_path(self, relative_path: str) -> Path:
        """Returns the resolved safe Path if it exists, raises FileNotFoundError otherwise."""
        safe_path = self.resolve_safe_path(relative_path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")
        return safe_path

    def read_file(self, relative_path: str) -> bytes:
        """Reads and returns binary content of a stored file safely."""
        return self.get_file_path(relative_path).read_bytes()


storage_service = StorageService()
