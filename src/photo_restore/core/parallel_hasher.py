"""Parallel hashing utilities for improved performance."""

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import imagehash
import pillow_heif
from PIL import Image

# Register HEIF/HEIC support
pillow_heif.register_heif_opener()


def _compute_sha256(path: str) -> tuple[str, Optional[str]]:
    """Compute SHA256 hash of a file. Returns (path, hash) tuple."""
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return (path, sha256.hexdigest())
    except (IOError, OSError):
        return (path, None)


def _compute_phash(path: str) -> tuple[str, Optional[str]]:
    """Compute perceptual hash of an image. Returns (path, hash) tuple."""
    try:
        with Image.open(path) as img:
            return (path, str(imagehash.phash(img)))
    except Exception:
        return (path, None)


def _compute_both_hashes(path: str) -> tuple[str, Optional[str], Optional[str]]:
    """Compute both SHA256 and perceptual hash. Returns (path, sha256, phash) tuple."""
    sha256 = None
    phash = None

    # Compute SHA256
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        sha256 = sha.hexdigest()
    except (IOError, OSError):
        pass

    # Compute phash for images
    ext = Path(path).suffix.lower()
    image_exts = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".webp", ".tiff", ".tif", ".bmp"}
    if ext in image_exts:
        try:
            with Image.open(path) as img:
                phash = str(imagehash.phash(img))
        except Exception:
            pass

    return (path, sha256, phash)


class ParallelHasher:
    """Parallel file hasher using multiprocessing."""

    def __init__(
        self,
        max_workers: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Initialize the parallel hasher.

        Args:
            max_workers: Maximum number of worker processes. Defaults to CPU count.
            progress_callback: Optional callback(completed, total) for progress updates.
        """
        self.max_workers = max_workers or os.cpu_count() or 4
        self.progress_callback = progress_callback

    def compute_sha256_batch(self, paths: list[Path]) -> dict[Path, Optional[str]]:
        """
        Compute SHA256 hashes for a batch of files in parallel.

        Returns a dict mapping path -> hash (or None if failed).
        """
        results: dict[Path, Optional[str]] = {}
        path_strs = [str(p) for p in paths]
        total = len(path_strs)
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_compute_sha256, p): p for p in path_strs}

            for future in as_completed(futures):
                path_str, hash_val = future.result()
                results[Path(path_str)] = hash_val
                completed += 1

                if self.progress_callback and completed % 10 == 0:
                    self.progress_callback(completed, total)

        if self.progress_callback:
            self.progress_callback(total, total)

        return results

    def compute_phash_batch(self, paths: list[Path]) -> dict[Path, Optional[str]]:
        """
        Compute perceptual hashes for a batch of image files in parallel.

        Returns a dict mapping path -> hash (or None if failed).
        """
        results: dict[Path, Optional[str]] = {}
        path_strs = [str(p) for p in paths]
        total = len(path_strs)
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_compute_phash, p): p for p in path_strs}

            for future in as_completed(futures):
                path_str, hash_val = future.result()
                results[Path(path_str)] = hash_val
                completed += 1

                if self.progress_callback and completed % 10 == 0:
                    self.progress_callback(completed, total)

        if self.progress_callback:
            self.progress_callback(total, total)

        return results

    def compute_all_hashes_batch(
        self, paths: list[Path]
    ) -> dict[Path, tuple[Optional[str], Optional[str]]]:
        """
        Compute both SHA256 and perceptual hashes in parallel.

        Returns a dict mapping path -> (sha256, phash).
        """
        results: dict[Path, tuple[Optional[str], Optional[str]]] = {}
        path_strs = [str(p) for p in paths]
        total = len(path_strs)
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_compute_both_hashes, p): p for p in path_strs}

            for future in as_completed(futures):
                path_str, sha256, phash = future.result()
                results[Path(path_str)] = (sha256, phash)
                completed += 1

                if self.progress_callback and completed % 10 == 0:
                    self.progress_callback(completed, total)

        if self.progress_callback:
            self.progress_callback(total, total)

        return results
