"""Amazon Photos folder scanner."""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

import imagehash
import pillow_heif
from PIL import Image
from PIL.ExifTags import TAGS

from models import LivePhoto, PhotoAsset

# Register HEIF/HEIC support with Pillow
pillow_heif.register_heif_opener()

# Supported image extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".webp", ".tiff", ".tif", ".bmp"}

# Video file extensions
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp"}

# All supported extensions
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def compute_sha256(path: Path) -> Optional[str]:
    """Compute SHA256 hash of a file."""
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except (IOError, OSError):
        return None


def compute_phash(path: Path) -> Optional[str]:
    """Compute perceptual hash of an image."""
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def get_image_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Get image dimensions."""
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def get_exif_date(path: Path) -> Optional[str]:
    """Extract EXIF date from image."""
    try:
        with Image.open(path) as img:
            exif = img._getexif()
            if not exif:
                return None

            # Look for DateTimeOriginal (36867) or DateTime (306)
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ("DateTimeOriginal", "DateTime"):
                    # Parse and convert to ISO format
                    try:
                        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        return dt.isoformat()
                    except ValueError:
                        pass
    except Exception:
        pass
    return None


def get_file_date(path: Path) -> Optional[str]:
    """Get file modification date as fallback."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        return None


class AmazonReader:
    """Scanner for Amazon Photos backup folder."""

    def __init__(self, folder: Path, verbose: bool = False):
        self.folder = Path(folder)
        self.verbose = verbose
        self._all_files: Optional[list[Path]] = None

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[Amazon] {message}")

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

    def _filter_heic_preference(self, files: list[Path]) -> list[Path]:
        """Filter files to prefer HEIC over JPG when both exist."""
        # Group files by base name (without extension)
        by_basename: dict[str, list[Path]] = {}
        for f in files:
            basename = f.stem.upper()  # Case-insensitive
            if basename not in by_basename:
                by_basename[basename] = []
            by_basename[basename].append(f)

        result = []
        for basename, paths in by_basename.items():
            if len(paths) == 1:
                result.append(paths[0])
            else:
                # Check if we have both HEIC and JPG
                heic_files = [p for p in paths if p.suffix.lower() in {".heic", ".heif"}]
                jpg_files = [p for p in paths if p.suffix.lower() in {".jpg", ".jpeg"}]
                other_files = [p for p in paths if p not in heic_files and p not in jpg_files]

                if heic_files and jpg_files:
                    # Prefer HEIC
                    self._log(f"Preferring HEIC over JPG for {basename}")
                    result.extend(heic_files)
                    result.extend(other_files)
                else:
                    result.extend(paths)

        return result

    def get_photos(self) -> Iterator[PhotoAsset]:
        """Get all photos/videos from the folder."""
        files = self._scan_files()
        files = self._filter_heic_preference(files)

        self._log(f"Processing {len(files)} files...")
        count = 0

        for path in files:
            asset = self._create_asset(path)
            if asset:
                count += 1
                yield asset

        self._log(f"Loaded {count} assets")

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

    def get_live_photos(self) -> Iterator[LivePhoto]:
        """Detect and return Live Photo pairs."""
        files = self._scan_files()
        files = self._filter_heic_preference(files)

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
        from parallel_hasher import ParallelHasher

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
