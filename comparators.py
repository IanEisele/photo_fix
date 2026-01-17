"""Comparison logic for matching photos between Amazon and iCloud."""

from datetime import datetime
from typing import Optional

import imagehash
from dateutil import parser as date_parser

from models import ComparisonResult, MatchResult, PhotoAsset


# Thresholds
PERCEPTUAL_MATCH_THRESHOLD = 5  # Hamming distance for definite match
PERCEPTUAL_UNCERTAIN_THRESHOLD = 10  # Hamming distance for uncertain match
SIZE_TOLERANCE = 0.05  # 5% tolerance for file size comparison
DATE_TOLERANCE_SECONDS = 60  # 1 minute tolerance for date comparison
VIDEO_DURATION_TOLERANCE = 1.0  # 1 second tolerance for video duration


def exact_match(amazon: PhotoAsset, icloud: PhotoAsset) -> Optional[ComparisonResult]:
    """Compare SHA256 hashes for exact match."""
    if amazon.sha256 and icloud.sha256:
        if amazon.sha256 == icloud.sha256:
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.EXACT,
                matched_icloud_asset=icloud,
                confidence=1.0,
                reason="SHA256 hash match",
            )
    return None


def perceptual_match(
    amazon: PhotoAsset,
    icloud: PhotoAsset,
    threshold: int = PERCEPTUAL_MATCH_THRESHOLD,
    uncertain_threshold: int = PERCEPTUAL_UNCERTAIN_THRESHOLD,
) -> Optional[ComparisonResult]:
    """Compare perceptual hashes for similar images."""
    if amazon.is_video or icloud.is_video:
        return None

    if not amazon.phash or not icloud.phash:
        return None

    try:
        amazon_hash = imagehash.hex_to_hash(amazon.phash)
        icloud_hash = imagehash.hex_to_hash(icloud.phash)
        distance = amazon_hash - icloud_hash

        if distance <= threshold:
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.PERCEPTUAL,
                matched_icloud_asset=icloud,
                confidence=1.0 - (distance / 64.0),  # 64 bits in phash
                reason=f"Perceptual hash match (distance={distance})",
            )
        elif distance <= uncertain_threshold:
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.UNCERTAIN,
                matched_icloud_asset=icloud,
                confidence=0.5 - (distance - threshold) / (64.0 - threshold),
                reason=f"Perceptual hash close match (distance={distance})",
            )
    except Exception:
        pass

    return None


def metadata_match(amazon: PhotoAsset, icloud: PhotoAsset) -> Optional[ComparisonResult]:
    """Compare metadata (date, dimensions, size) for match."""
    # Check dimensions match
    if amazon.dimensions and icloud.dimensions:
        if amazon.dimensions != icloud.dimensions:
            return None
    elif amazon.dimensions or icloud.dimensions:
        # One has dimensions, the other doesn't - can't match on dimensions
        pass

    # Check date match
    if amazon.exif_date and icloud.exif_date:
        try:
            amazon_dt = date_parser.parse(amazon.exif_date)
            icloud_dt = date_parser.parse(icloud.exif_date)
            date_diff = abs((amazon_dt - icloud_dt).total_seconds())
            if date_diff > DATE_TOLERANCE_SECONDS:
                return None
        except (ValueError, TypeError):
            return None
    else:
        # No date info - can't match on metadata
        return None

    # Check file size (with tolerance for re-encoding)
    size_ratio = amazon.file_size / icloud.file_size if icloud.file_size else 0
    if not (1 - SIZE_TOLERANCE <= size_ratio <= 1 + SIZE_TOLERANCE):
        # Sizes don't match closely - might still be the same image
        # but confidence is lower
        confidence = 0.6
        reason = f"Metadata match (date match, dimensions match, size ratio={size_ratio:.2f})"
    else:
        confidence = 0.8
        reason = "Metadata match (date, dimensions, size all match)"

    return ComparisonResult(
        amazon_asset=amazon,
        match_type=MatchResult.METADATA,
        matched_icloud_asset=icloud,
        confidence=confidence,
        reason=reason,
    )


def video_match(amazon: PhotoAsset, icloud: PhotoAsset) -> Optional[ComparisonResult]:
    """Compare video metadata for match."""
    if not amazon.is_video or not icloud.is_video:
        return None

    confidence_factors = []
    reasons = []

    # Check duration
    if amazon.duration is not None and icloud.duration is not None:
        duration_diff = abs(amazon.duration - icloud.duration)
        if duration_diff <= VIDEO_DURATION_TOLERANCE:
            confidence_factors.append(0.9)
            reasons.append("duration match")
        else:
            return None  # Duration mismatch is a strong indicator of different videos

    # Check dimensions
    if amazon.dimensions and icloud.dimensions:
        if amazon.dimensions == icloud.dimensions:
            confidence_factors.append(0.7)
            reasons.append("dimensions match")
        else:
            return None  # Different dimensions

    # Check date
    if amazon.exif_date and icloud.exif_date:
        try:
            amazon_dt = date_parser.parse(amazon.exif_date)
            icloud_dt = date_parser.parse(icloud.exif_date)
            date_diff = abs((amazon_dt - icloud_dt).total_seconds())
            if date_diff <= DATE_TOLERANCE_SECONDS:
                confidence_factors.append(0.8)
                reasons.append("date match")
            else:
                return None  # Different dates
        except (ValueError, TypeError):
            pass

    # Check file size ratio (for re-encoding detection)
    if amazon.file_size and icloud.file_size:
        size_ratio = amazon.file_size / icloud.file_size
        if 0.5 <= size_ratio <= 2.0:
            # Within reasonable re-encoding range
            if 0.9 <= size_ratio <= 1.1:
                confidence_factors.append(0.6)
                reasons.append("size match")
            else:
                reasons.append(f"size ratio {size_ratio:.2f}")

    if not confidence_factors:
        return None

    # Average confidence from all factors
    confidence = sum(confidence_factors) / len(confidence_factors)
    reason = f"Video match ({', '.join(reasons)})"

    return ComparisonResult(
        amazon_asset=amazon,
        match_type=MatchResult.METADATA,
        matched_icloud_asset=icloud,
        confidence=confidence,
        reason=reason,
    )


def compare(
    amazon: PhotoAsset,
    icloud_assets: list[PhotoAsset],
    perceptual_threshold: int = PERCEPTUAL_MATCH_THRESHOLD,
) -> ComparisonResult:
    """
    Compare an Amazon asset against all iCloud assets.

    Tries matching strategies in priority order:
    1. Exact SHA256 match
    2. Perceptual hash match (images only)
    3. Metadata match
    4. Video match (videos only)

    Returns the best match found, or NO_MATCH if none found.
    """
    best_result: Optional[ComparisonResult] = None

    for icloud in icloud_assets:
        # Try exact match first
        result = exact_match(amazon, icloud)
        if result:
            return result  # Exact match is definitive

        # Try perceptual match for images
        if not amazon.is_video:
            result = perceptual_match(amazon, icloud, perceptual_threshold)
            if result:
                if result.match_type == MatchResult.PERCEPTUAL:
                    return result  # High-confidence perceptual match
                elif best_result is None or result.confidence > best_result.confidence:
                    best_result = result

        # Try video match
        if amazon.is_video:
            result = video_match(amazon, icloud)
            if result and (best_result is None or result.confidence > best_result.confidence):
                best_result = result
        else:
            # Try metadata match for images
            result = metadata_match(amazon, icloud)
            if result and (best_result is None or result.confidence > best_result.confidence):
                best_result = result

    if best_result:
        return best_result

    return ComparisonResult(
        amazon_asset=amazon,
        match_type=MatchResult.NO_MATCH,
        matched_icloud_asset=None,
        confidence=0.0,
        reason="No match found in iCloud library",
    )


class PhotoComparator:
    """Comparator class for batch photo comparison."""

    def __init__(
        self,
        icloud_assets: list[PhotoAsset],
        perceptual_threshold: int = PERCEPTUAL_MATCH_THRESHOLD,
        verbose: bool = False,
    ):
        self.icloud_assets = icloud_assets
        self.perceptual_threshold = perceptual_threshold
        self.verbose = verbose

        # Build lookup indexes for faster matching
        self._by_sha256: dict[str, PhotoAsset] = {}
        self._by_dimensions_date: dict[tuple, list[PhotoAsset]] = {}

        for asset in icloud_assets:
            if asset.sha256:
                self._by_sha256[asset.sha256] = asset

            key = (asset.dimensions, asset.exif_date[:10] if asset.exif_date else None)
            if key not in self._by_dimensions_date:
                self._by_dimensions_date[key] = []
            self._by_dimensions_date[key].append(asset)

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[Comparator] {message}")

    def compare_asset(self, amazon: PhotoAsset) -> ComparisonResult:
        """Compare a single Amazon asset against iCloud library."""
        # Quick SHA256 lookup first
        if amazon.sha256 and amazon.sha256 in self._by_sha256:
            icloud = self._by_sha256[amazon.sha256]
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.EXACT,
                matched_icloud_asset=icloud,
                confidence=1.0,
                reason="SHA256 hash match",
            )

        # Try narrowed search based on dimensions and date
        candidates = []
        key = (amazon.dimensions, amazon.exif_date[:10] if amazon.exif_date else None)
        if key in self._by_dimensions_date:
            candidates = self._by_dimensions_date[key]

        # If no candidates from index, fall back to full comparison
        if not candidates:
            candidates = self.icloud_assets

        return compare(amazon, candidates, self.perceptual_threshold)

    def compare_all(self, amazon_assets: list[PhotoAsset]) -> list[ComparisonResult]:
        """Compare all Amazon assets against iCloud library."""
        results = []
        total = len(amazon_assets)

        for i, amazon in enumerate(amazon_assets):
            if self.verbose and i % 100 == 0:
                self._log(f"Comparing {i+1}/{total}...")

            result = self.compare_asset(amazon)
            results.append(result)

        return results
