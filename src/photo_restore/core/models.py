"""Data models for photo comparison."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class MatchResult(Enum):
    """Result types for photo comparison."""

    EXACT = "exact"  # SHA256 match
    PERCEPTUAL = "perceptual"  # Perceptual hash match (high confidence)
    METADATA = "metadata"  # Metadata match (date, dimensions, size)
    UNCERTAIN = "uncertain"  # Close perceptual match, needs review
    NO_MATCH = "no_match"  # No match found


@dataclass
class PhotoAsset:
    """Represents a photo or video asset."""

    path: Path
    file_size: int
    is_video: bool = False

    # Lazily computed fields
    sha256: Optional[str] = None
    phash: Optional[str] = None
    exif_date: Optional[str] = None
    dimensions: Optional[tuple[int, int]] = None
    duration: Optional[float] = None  # For videos, in seconds

    # iCloud-specific fields
    icloud_uuid: Optional[str] = None

    def __hash__(self) -> int:
        return hash(self.path)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PhotoAsset):
            return False
        return self.path == other.path


@dataclass
class LivePhoto:
    """Represents a Live Photo (image + video pair)."""

    image_asset: PhotoAsset
    video_asset: Optional[PhotoAsset] = None

    @property
    def is_complete(self) -> bool:
        """Check if both image and video components exist."""
        return self.video_asset is not None


@dataclass
class ComparisonResult:
    """Result of comparing an Amazon asset against iCloud library."""

    amazon_asset: PhotoAsset
    match_type: MatchResult
    matched_icloud_asset: Optional[PhotoAsset] = None
    confidence: float = 0.0
    reason: str = ""

    @property
    def is_missing(self) -> bool:
        """Check if the asset is missing from iCloud."""
        return self.match_type == MatchResult.NO_MATCH

    @property
    def needs_review(self) -> bool:
        """Check if the match needs manual review."""
        return self.match_type == MatchResult.UNCERTAIN


@dataclass
class LivePhotoComparisonResult:
    """Result of comparing a Live Photo pair."""

    amazon_live_photo: LivePhoto
    image_result: ComparisonResult
    video_result: Optional[ComparisonResult] = None

    @property
    def is_missing(self) -> bool:
        """Check if the Live Photo is missing from iCloud."""
        if self.video_result is None:
            return self.image_result.is_missing
        return self.image_result.is_missing or self.video_result.is_missing


@dataclass
class ProcessingStats:
    """Statistics for the processing run."""

    total_amazon_files: int = 0
    total_icloud_files: int = 0
    exact_matches: int = 0
    perceptual_matches: int = 0
    metadata_matches: int = 0
    uncertain_matches: int = 0
    missing_files: int = 0
    errors: int = 0
    live_photos_processed: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_amazon_files": self.total_amazon_files,
            "total_icloud_files": self.total_icloud_files,
            "exact_matches": self.exact_matches,
            "perceptual_matches": self.perceptual_matches,
            "metadata_matches": self.metadata_matches,
            "uncertain_matches": self.uncertain_matches,
            "missing_files": self.missing_files,
            "errors": self.errors,
            "live_photos_processed": self.live_photos_processed,
        }
