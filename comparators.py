"""Comparison logic for matching photos between Amazon and iCloud."""

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import imagehash
from dateutil import parser as date_parser

from models import ComparisonResult, MatchResult, PhotoAsset


# Thresholds
PERCEPTUAL_MATCH_THRESHOLD = 5  # Hamming distance for definite match
PERCEPTUAL_UNCERTAIN_THRESHOLD = 10  # Hamming distance for uncertain match
SIZE_TOLERANCE = 0.15  # 15% tolerance for file size comparison (handles re-encoding)
DATE_TOLERANCE_SECONDS = 300  # 5 minutes tolerance for date comparison (handles timestamp drift)
VIDEO_DURATION_TOLERANCE = 1.0  # 1 second tolerance for video duration
DIMENSION_TOLERANCE = 0.02  # 2% tolerance for dimension differences


def dimensions_match(dim1: tuple[int, int], dim2: tuple[int, int], tolerance: float = DIMENSION_TOLERANCE) -> bool:
    """Check if dimensions match within tolerance, accounting for rotation."""
    w1, h1 = dim1
    w2, h2 = dim2

    # Check normal orientation
    width_ratio = min(w1, w2) / max(w1, w2) if max(w1, w2) > 0 else 0
    height_ratio = min(h1, h2) / max(h1, h2) if max(h1, h2) > 0 else 0
    if width_ratio >= (1 - tolerance) and height_ratio >= (1 - tolerance):
        return True

    # Check rotated orientation (90° rotation swaps width and height)
    width_ratio_rotated = min(w1, h2) / max(w1, h2) if max(w1, h2) > 0 else 0
    height_ratio_rotated = min(h1, w2) / max(h1, w2) if max(h1, w2) > 0 else 0
    return width_ratio_rotated >= (1 - tolerance) and height_ratio_rotated >= (1 - tolerance)


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
    # Check date match first - required
    if not amazon.exif_date or not icloud.exif_date:
        return None

    try:
        amazon_dt = date_parser.parse(amazon.exif_date)
        icloud_dt = date_parser.parse(icloud.exif_date)
        date_diff = abs((amazon_dt - icloud_dt).total_seconds())
        if date_diff > DATE_TOLERANCE_SECONDS:
            return None
    except (ValueError, TypeError):
        return None

    # Check dimensions - if both have dimensions, apply tolerance
    dimensions_matched = False
    if amazon.dimensions and icloud.dimensions:
        if dimensions_match(amazon.dimensions, icloud.dimensions):
            dimensions_matched = True
        # If dimensions don't match within tolerance, don't return None immediately
        # Fall through to check file size

    # Check file size
    size_ratio = amazon.file_size / icloud.file_size if icloud.file_size else 0
    size_matched = (1 - SIZE_TOLERANCE) <= size_ratio <= (1 + SIZE_TOLERANCE)

    # Determine confidence based on what matched
    if dimensions_matched and size_matched:
        confidence = 0.85
        reason = "Metadata match (date, dimensions, size all match)"
    elif dimensions_matched:
        confidence = 0.7
        reason = f"Metadata match (date + dimensions, size ratio={size_ratio:.2f})"
    elif size_matched:
        confidence = 0.65
        reason = "Metadata match (date + size, dimensions differ)"
    else:
        # Only date matches - low confidence
        confidence = 0.5
        reason = f"Weak metadata match (date only, size ratio={size_ratio:.2f})"

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

    # Check duration - optional, add confidence if available and matching
    if amazon.duration is not None and icloud.duration is not None:
        duration_diff = abs(amazon.duration - icloud.duration)
        if duration_diff <= VIDEO_DURATION_TOLERANCE:
            confidence_factors.append(0.9)
            reasons.append("duration match")
        elif duration_diff <= VIDEO_DURATION_TOLERANCE * 3:  # 3 second grace period
            confidence_factors.append(0.6)
            reasons.append(f"duration close (diff={duration_diff:.1f}s)")
        else:
            return None  # Too different
    # If duration missing, don't fail - just skip this factor

    # Check dimensions - use tolerance instead of exact match
    if amazon.dimensions and icloud.dimensions:
        if dimensions_match(amazon.dimensions, icloud.dimensions):
            confidence_factors.append(0.7)
            reasons.append("dimensions match")
        else:
            return None  # Dimensions don't match within tolerance

    # Check date - required for video matching
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
            return None
    else:
        return None  # Both must have dates

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


def compute_phash_for_asset(path: Path) -> Optional[str]:
    """Compute perceptual hash for a single image file."""
    try:
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()

        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


class PhotoComparator:
    """Comparator class for batch photo comparison with lazy hashing support."""

    def __init__(
        self,
        icloud_assets: list[PhotoAsset],
        perceptual_threshold: int = PERCEPTUAL_MATCH_THRESHOLD,
        verbose: bool = False,
        lazy_phash: bool = True,
        phash_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the comparator.

        Args:
            icloud_assets: List of iCloud assets to compare against.
            perceptual_threshold: Hamming distance threshold for perceptual match.
            verbose: Enable verbose logging.
            lazy_phash: If True, compute perceptual hashes only when needed.
            phash_callback: Optional callback when computing lazy phash.
        """
        self.icloud_assets = icloud_assets
        self.perceptual_threshold = perceptual_threshold
        self.verbose = verbose
        self.lazy_phash = lazy_phash
        self.phash_callback = phash_callback

        # Build lookup indexes for faster matching
        self._by_sha256: dict[str, PhotoAsset] = {}
        self._by_dimensions_date: dict[tuple, list[PhotoAsset]] = {}
        self._by_date: dict[str, list[PhotoAsset]] = {}  # Date-only index for fallback
        self._phash_index: dict[str, list[PhotoAsset]] = {}  # Bucketed by phash prefix

        for asset in icloud_assets:
            if asset.sha256:
                self._by_sha256[asset.sha256] = asset

            key = (asset.dimensions, asset.exif_date[:10] if asset.exif_date else None)
            if key not in self._by_dimensions_date:
                self._by_dimensions_date[key] = []
            self._by_dimensions_date[key].append(asset)

            # Build date-only index for fallback matching
            if asset.exif_date:
                date_key = asset.exif_date[:10]
                if date_key not in self._by_date:
                    self._by_date[date_key] = []
                self._by_date[date_key].append(asset)

            # Build perceptual hash index (bucket by first 4 hex chars for locality)
            if asset.phash:
                prefix = asset.phash[:4]
                if prefix not in self._phash_index:
                    self._phash_index[prefix] = []
                self._phash_index[prefix].append(asset)

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[Comparator] {message}")

    def _ensure_phash(self, asset: PhotoAsset) -> None:
        """Ensure the asset has a perceptual hash computed (lazy computation)."""
        if asset.phash is None and not asset.is_video:
            if self.phash_callback:
                self.phash_callback(f"Computing phash for {asset.path.name}")
            asset.phash = compute_phash_for_asset(asset.path)

    def _get_phash_candidates(self, amazon: PhotoAsset) -> set[PhotoAsset]:
        """Get candidate iCloud assets based on perceptual hash prefix."""
        if not amazon.phash:
            return set()

        candidates: set[PhotoAsset] = set()
        prefix = amazon.phash[:4]

        # Check exact prefix match
        if prefix in self._phash_index:
            candidates.update(self._phash_index[prefix])

        # Check neighboring prefixes - expanded deltas for better coverage
        try:
            prefix_int = int(prefix, 16)
            # Include more neighbors: +/-1, +/-16, +/-256, +/-4096, and diagonals
            deltas = [-1, 1, -16, 16, -17, 17, -15, 15, -256, 256, -4096, 4096]
            for delta in deltas:
                neighbor = prefix_int + delta
                if 0 <= neighbor <= 0xFFFF:
                    neighbor_prefix = f"{neighbor:04x}"
                    if neighbor_prefix in self._phash_index:
                        candidates.update(self._phash_index[neighbor_prefix])
        except ValueError:
            pass

        return candidates

    def compare_asset(self, amazon: PhotoAsset) -> ComparisonResult:
        """Compare a single Amazon asset against iCloud library."""
        # 1. Try exact SHA256 match first (fastest)
        if amazon.sha256 and amazon.sha256 in self._by_sha256:
            icloud = self._by_sha256[amazon.sha256]
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.EXACT,
                matched_icloud_asset=icloud,
                confidence=1.0,
                reason="SHA256 hash match",
            )

        # 2. Ensure phash is computed for perceptual matching
        if self.lazy_phash:
            self._ensure_phash(amazon)

        # 3. Try perceptual hash candidates
        if not amazon.is_video and amazon.phash:
            phash_candidates = self._get_phash_candidates(amazon)
            if phash_candidates:
                for icloud in phash_candidates:
                    result = perceptual_match(amazon, icloud, self.perceptual_threshold)
                    if result and result.match_type == MatchResult.PERCEPTUAL:
                        return result

        # 4. Try dimension+date index
        candidates = []
        key = (amazon.dimensions, amazon.exif_date[:10] if amazon.exif_date else None)
        if key in self._by_dimensions_date:
            candidates = self._by_dimensions_date[key]

        # 5. If no exact dimension match, use date-only index with dimension tolerance
        if not candidates and amazon.exif_date:
            date_key = amazon.exif_date[:10]
            if date_key in self._by_date:
                candidates = self._by_date[date_key]

        # 6. Final fallback: full comparison against all assets
        if not candidates:
            candidates = self.icloud_assets

        return compare(amazon, candidates, self.perceptual_threshold)

    def compare_all(
        self,
        amazon_assets: list[PhotoAsset],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[ComparisonResult]:
        """
        Compare all Amazon assets against iCloud library.

        Args:
            amazon_assets: List of Amazon assets to compare.
            progress_callback: Optional callback(completed, total) for progress.

        Returns:
            List of comparison results.
        """
        results = []
        total = len(amazon_assets)

        for i, amazon in enumerate(amazon_assets):
            if self.verbose and i % 100 == 0:
                self._log(f"Comparing {i+1}/{total}...")

            if progress_callback and i % 10 == 0:
                progress_callback(i, total)

            result = self.compare_asset(amazon)
            results.append(result)

        if progress_callback:
            progress_callback(total, total)

        return results
