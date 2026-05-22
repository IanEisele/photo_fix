"""Base reader class for photo folder scanning."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterator, Optional

from photo_restore.core.constants import ALL_EXTENSIONS, VIDEO_EXTENSIONS
from photo_restore.core.hashing import (
    compute_phash,
    compute_sha256,
    get_exif_date,
    get_file_date,
    get_image_dimensions,
)
from photo_restore.core.models import LivePhoto, PhotoAsset
from photo_restore.core.parallel_hasher import ParallelHasher


class BaseReader(ABC):
    """Abstract base class for photo folder readers."""

    def __init__(self, folder: Path, verbose: bool = False):
        self.folder = Path(folder)
        self.verbose = verbose
        self._all_files: Optional[list[Path]] = None

    @property
    @abstractmethod
    def log_prefix(self) -> str:
        """Return the prefix to use for log messages."""
        pass

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[{self.log_prefix}] {message}")

    def _scan_files(self) -> list[Path]:
        """Scan folder for all media files."""
        if self._all_files is not None:
            return self._all_files

        self._log(f"Scanning folder: {self.folder}")
        files = []

        for root, _, filenames in os.walk(self.folder):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in ALL_EXTENSIONS:
                    files.append(Path(root) / filename)

        self._log(f"Found {len(files)} media files")
        self._all_files = files
        return files

    def _create_asset(self, path: Path) -> Optional[PhotoAsset]:
        """Create a PhotoAsset from a file path."""
        try:
            file_size = path.stat().st_size
        except OSError:
            return None

        ext = path.suffix.lower()
        is_video = ext in VIDEO_EXTENSIONS

        # Get EXIF date for images, file date for videos
        if is_video:
            exif_date = get_file_date(path)
            dimensions = None
        else:
            exif_date = get_exif_date(path) or get_file_date(path)
            dimensions = get_image_dimensions(path)

        return PhotoAsset(
            path=path,
            file_size=file_size,
            is_video=is_video,
            exif_date=exif_date,
            dimensions=dimensions,
        )

    def _get_files_for_processing(self) -> list[Path]:
        """Get files for processing. Subclasses can override to filter."""
        return self._scan_files()

    def get_photos(self) -> Iterator[PhotoAsset]:
        """Get all photos/videos from the folder."""
        files = self._get_files_for_processing()
        self._log(f"Processing {len(files)} files...")
        count = 0

        for path in files:
            asset = self._create_asset(path)
            if asset:
                count += 1
                yield asset

        self._log(f"Loaded {count} assets")

    def get_live_photos(self) -> Iterator[LivePhoto]:
        """Detect and return Live Photo pairs."""
        files = self._get_files_for_processing()

        # Group by base filename to find pairs
        by_basename: dict[str, dict[str, Path]] = {}
        for f in files:
            basename = f.stem.upper()
            ext = f.suffix.lower()
            if basename not in by_basename:
                by_basename[basename] = {}
            by_basename[basename][ext] = f

        self._log("Detecting Live Photo pairs...")
        count = 0

        for basename, extensions in by_basename.items():
            # Check for image + MOV pair
            image_path = None
            video_path = None

            for ext in [".heic", ".heif", ".jpg", ".jpeg"]:
                if ext in extensions:
                    image_path = extensions[ext]
                    break

            if ".mov" in extensions:
                video_path = extensions[".mov"]

            if image_path and video_path:
                image_asset = self._create_asset(image_path)
                video_asset = self._create_asset(video_path)

                if image_asset:
                    live_photo = LivePhoto(
                        image_asset=image_asset,
                        video_asset=video_asset,
                    )
                    count += 1
                    yield live_photo

        self._log(f"Found {count} Live Photo pairs")

    def compute_hashes_for_asset(self, asset: PhotoAsset) -> PhotoAsset:
        """Compute SHA256 and perceptual hash for an asset."""
        if asset.sha256 is None:
            asset.sha256 = compute_sha256(asset.path)

        if not asset.is_video and asset.phash is None:
            asset.phash = compute_phash(asset.path)

        return asset

    def compute_hashes_parallel(
        self,
        photos: list[PhotoAsset],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        compute_phash: bool = True,
    ) -> None:
        """
        Compute hashes for all photos using parallel processing.

        Args:
            photos: List of PhotoAsset objects to hash.
            progress_callback: Optional callback(completed, total, message) for progress.
            compute_phash: If True, also compute perceptual hashes.
        """
        paths = [p.path for p in photos]
        path_to_asset = {p.path: p for p in photos}

        def on_progress(completed: int, total: int) -> None:
            if progress_callback:
                progress_callback(completed, total, f"Hashing {completed}/{total} files...")

        hasher = ParallelHasher(progress_callback=on_progress)

        if compute_phash:
            # Compute both hashes together
            results = hasher.compute_all_hashes_batch(paths)
            for path, (sha256, phash) in results.items():
                asset = path_to_asset[path]
                asset.sha256 = sha256
                if not asset.is_video:
                    asset.phash = phash
        else:
            # Just compute SHA256
            results = hasher.compute_sha256_batch(paths)
            for path, sha256 in results.items():
                path_to_asset[path].sha256 = sha256

    def load_all(self) -> tuple[list[PhotoAsset], list[LivePhoto]]:
        """Load all photos and Live Photos."""
        photos = list(self.get_photos())
        live_photos = list(self.get_live_photos())
        return photos, live_photos
