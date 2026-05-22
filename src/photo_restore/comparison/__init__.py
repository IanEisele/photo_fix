"""Comparison logic for photo matching."""

from photo_restore.comparison.comparators import (
    DIMENSION_TOLERANCE,
    PERCEPTUAL_MATCH_THRESHOLD,
    PERCEPTUAL_UNCERTAIN_THRESHOLD,
    PhotoComparator,
    compare,
    compute_phash_for_asset,
    dimensions_match,
    exact_match,
    metadata_match,
    perceptual_match,
    video_match,
)
from photo_restore.comparison.live_photos import LivePhotoHandler

__all__ = [
    "DIMENSION_TOLERANCE",
    "PERCEPTUAL_MATCH_THRESHOLD",
    "PERCEPTUAL_UNCERTAIN_THRESHOLD",
    "PhotoComparator",
    "compare",
    "compute_phash_for_asset",
    "dimensions_match",
    "exact_match",
    "metadata_match",
    "perceptual_match",
    "video_match",
    "LivePhotoHandler",
]
