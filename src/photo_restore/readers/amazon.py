"""Amazon Photos folder scanner."""

from pathlib import Path
from typing import Iterator

from photo_restore.core.models import LivePhoto, PhotoAsset
from photo_restore.readers.base import BaseReader


class AmazonReader(BaseReader):
    """Scanner for Amazon Photos backup folder."""

    @property
    def log_prefix(self) -> str:
        return "Amazon"

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

    def _get_files_for_processing(self) -> list[Path]:
        """Get files for processing with HEIC preference applied."""
        files = self._scan_files()
        return self._filter_heic_preference(files)

    def get_photos(self) -> Iterator[PhotoAsset]:
        """Get all photos/videos from the folder with HEIC preference."""
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
        """Detect and return Live Photo pairs with HEIC preference."""
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
