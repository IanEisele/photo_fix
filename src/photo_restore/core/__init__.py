"""Core modules for photo_restore."""

from photo_restore.core.constants import ALL_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from photo_restore.core.hashing import (
    compute_phash,
    compute_sha256,
    get_exif_date,
    get_file_date,
    get_image_dimensions,
)
from photo_restore.core.models import (
    ComparisonResult,
    LivePhoto,
    LivePhotoComparisonResult,
    MatchResult,
    PhotoAsset,
    ProcessingStats,
)
from photo_restore.core.parallel_hasher import ParallelHasher

__all__ = [
    "ALL_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "compute_phash",
    "compute_sha256",
    "get_exif_date",
    "get_file_date",
    "get_image_dimensions",
    "ComparisonResult",
    "LivePhoto",
    "LivePhotoComparisonResult",
    "MatchResult",
    "PhotoAsset",
    "ProcessingStats",
    "ParallelHasher",
]
