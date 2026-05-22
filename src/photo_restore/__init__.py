"""
Photo Restore Tool

Compare Amazon Photos backup against iCloud Photos export
and identify missing files for restoration.
"""

from photo_restore.core import (
    ALL_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ComparisonResult,
    LivePhoto,
    LivePhotoComparisonResult,
    MatchResult,
    PhotoAsset,
    ProcessingStats,
)
from photo_restore.readers import AmazonReader, ICloudReader

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ALL_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "ComparisonResult",
    "LivePhoto",
    "LivePhotoComparisonResult",
    "MatchResult",
    "PhotoAsset",
    "ProcessingStats",
    "AmazonReader",
    "ICloudReader",
]
