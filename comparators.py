"""Comparison logic for matching photos between Amazon and iCloud."""

from pathlib import Path
from typing import Callable, Optional

import imagehash
from dateutil import parser as date_parser

from models import ComparisonResult, MatchResult, PhotoAsset

# Thresholds
PERCEPTUAL_MATCH_THRESHOLD = 5
PERCEPTUAL_UNCERTAIN_THRESHOLD = 10
SIZE_TOLERANCE = 0.15
DATE_TOLERANCE_SECONDS = 300
VIDEO_DURATION_TOLERANCE = 1.0
DIMENSION_TOLERANCE = 0.02


def _dimension_ratio(a: int, b: int) -> float:
    """Calculate the ratio of two dimensions (0.0 to 1.0)."""
    if max(a, b) == 0:
        return 0.0
    return min(a, b) / max(a, b)


def dimensions_match(dim1: tuple[int, int], dim2: tuple[int, int], tolerance: float = DIMENSION_TOLERANCE) -> bool:
    """Check if dimensions match within tolerance, accounting for rotation."""
    w1, h1 = dim1
    w2, h2 = dim2
    min_ratio = 1 - tolerance

    # Check normal orientation
    if _dimension_ratio(w1, w2) >= min_ratio and _dimension_ratio(h1, h2) >= min_ratio:
        return True

    # Check rotated orientation (90 degree rotation swaps width and height)
    return _dimension_ratio(w1, h2) >= min_ratio and _dimension_ratio(h1, w2) >= min_ratio


def _dates_match(date1: Optional[str], date2: Optional[str], tolerance_seconds: float = DATE_TOLERANCE_SECONDS) -> bool:
    """Check if two date strings match within tolerance."""
    if not date1 or not date2:
        return False
    try:
        dt1 = date_parser.parse(date1)
        dt2 = date_parser.parse(date2)
        return abs((dt1 - dt2).total_seconds()) <= tolerance_seconds
    except (ValueError, TypeError):
        return False


def exact_match(amazon: PhotoAsset, icloud: PhotoAsset) -> Optional[ComparisonResult]:
    """Compare SHA256 hashes for exact match."""
    if not amazon.sha256 or not icloud.sha256 or amazon.sha256 != icloud.sha256:
        return None

    return ComparisonResult(
        amazon_asset=amazon,
        match_type=MatchResult.EXACT,
        matched_icloud_asset=icloud,
        confidence=1.0,
        reason="SHA256 hash match",
    )


def _compute_hash_distance(phash1: str, phash2: str) -> Optional[int]:
    """Compute Hamming distance between two perceptual hashes."""
    try:
        hash1 = imagehash.hex_to_hash(phash1)
        hash2 = imagehash.hex_to_hash(phash2)
        return hash1 - hash2
    except Exception:
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

    distance = _compute_hash_distance(amazon.phash, icloud.phash)
    if distance is None:
        return None

    if distance <= threshold:
        return ComparisonResult(
            amazon_asset=amazon,
            match_type=MatchResult.PERCEPTUAL,
            matched_icloud_asset=icloud,
            confidence=1.0 - (distance / 64.0),
            reason=f"Perceptual hash match (distance={distance})",
        )

    if distance <= uncertain_threshold:
        return ComparisonResult(
            amazon_asset=amazon,
            match_type=MatchResult.UNCERTAIN,
            matched_icloud_asset=icloud,
            confidence=0.5 - (distance - threshold) / (64.0 - threshold),
            reason=f"Perceptual hash close match (distance={distance})",
        )

    return None


def _compute_size_ratio(size1: int, size2: int) -> float:
    """Compute file size ratio, returning 0.0 if denominator is zero."""
    if size2 == 0:
        return 0.0
    return size1 / size2


def metadata_match(amazon: PhotoAsset, icloud: PhotoAsset) -> Optional[ComparisonResult]:
    """Compare metadata (date, dimensions, size) for match."""
    if not _dates_match(amazon.exif_date, icloud.exif_date):
        return None

    has_dimensions = amazon.dimensions and icloud.dimensions
    dimensions_matched = has_dimensions and dimensions_match(amazon.dimensions, icloud.dimensions)

    size_ratio = _compute_size_ratio(amazon.file_size, icloud.file_size)
    size_matched = (1 - SIZE_TOLERANCE) <= size_ratio <= (1 + SIZE_TOLERANCE)

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

    # Date match is required
    if not _dates_match(amazon.exif_date, icloud.exif_date):
        return None

    # Dimensions must match if both are available
    if amazon.dimensions and icloud.dimensions:
        if not dimensions_match(amazon.dimensions, icloud.dimensions):
            return None

    confidence_factors = []
    reasons = []

    # Add date confidence
    confidence_factors.append(0.8)
    reasons.append("date match")

    # Add dimensions confidence if available
    if amazon.dimensions and icloud.dimensions:
        confidence_factors.append(0.7)
        reasons.append("dimensions match")

    # Check duration if available
    if amazon.duration is not None and icloud.duration is not None:
        duration_diff = abs(amazon.duration - icloud.duration)
        if duration_diff > VIDEO_DURATION_TOLERANCE * 3:
            return None
        if duration_diff <= VIDEO_DURATION_TOLERANCE:
            confidence_factors.append(0.9)
            reasons.append("duration match")
        else:
            confidence_factors.append(0.6)
            reasons.append(f"duration close (diff={duration_diff:.1f}s)")

    # Check file size ratio for re-encoding detection
    if amazon.file_size and icloud.file_size:
        size_ratio = _compute_size_ratio(amazon.file_size, icloud.file_size)
        if 0.5 <= size_ratio <= 2.0:
            if 0.9 <= size_ratio <= 1.1:
                confidence_factors.append(0.6)
                reasons.append("size match")
            else:
                reasons.append(f"size ratio {size_ratio:.2f}")

    if not confidence_factors:
        return None

    confidence = sum(confidence_factors) / len(confidence_factors)

    return ComparisonResult(
        amazon_asset=amazon,
        match_type=MatchResult.METADATA,
        matched_icloud_asset=icloud,
        confidence=confidence,
        reason=f"Video match ({', '.join(reasons)})",
    )


def _update_best_result(
    current_best: Optional[ComparisonResult],
    candidate: Optional[ComparisonResult],
) -> Optional[ComparisonResult]:
    """Return the higher confidence result between current best and candidate."""
    if candidate is None:
        return current_best
    if current_best is None or candidate.confidence > current_best.confidence:
        return candidate
    return current_best


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
    3. Metadata match (images) or Video match (videos)

    Returns the best match found, or NO_MATCH if none found.
    """
    best_result: Optional[ComparisonResult] = None

    for icloud in icloud_assets:
        result = exact_match(amazon, icloud)
        if result:
            return result

        if amazon.is_video:
            best_result = _update_best_result(best_result, video_match(amazon, icloud))
        else:
            result = perceptual_match(amazon, icloud, perceptual_threshold)
            if result and result.match_type == MatchResult.PERCEPTUAL:
                return result
            best_result = _update_best_result(best_result, result)
            best_result = _update_best_result(best_result, metadata_match(amazon, icloud))

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
        self.icloud_assets = icloud_assets
        self.perceptual_threshold = perceptual_threshold
        self.verbose = verbose
        self.lazy_phash = lazy_phash
        self.phash_callback = phash_callback

        self._by_sha256: dict[str, PhotoAsset] = {}
        self._by_dimensions_date: dict[tuple, list[PhotoAsset]] = {}
        self._by_date: dict[str, list[PhotoAsset]] = {}
        self._phash_index: dict[str, list[PhotoAsset]] = {}

        self._build_indexes(icloud_assets)

    def _build_indexes(self, assets: list[PhotoAsset]) -> None:
        """Build lookup indexes for faster matching."""
        for asset in assets:
            if asset.sha256:
                self._by_sha256[asset.sha256] = asset

            dim_date_key = (asset.dimensions, asset.exif_date[:10] if asset.exif_date else None)
            self._by_dimensions_date.setdefault(dim_date_key, []).append(asset)

            if asset.exif_date:
                date_key = asset.exif_date[:10]
                self._by_date.setdefault(date_key, []).append(asset)

            if asset.phash:
                phash_prefix = asset.phash[:4]
                self._phash_index.setdefault(phash_prefix, []).append(asset)

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

        if prefix in self._phash_index:
            candidates.update(self._phash_index[prefix])

        self._add_neighboring_prefix_candidates(prefix, candidates)
        return candidates

    def _add_neighboring_prefix_candidates(self, prefix: str, candidates: set[PhotoAsset]) -> None:
        """Add candidates from neighboring phash prefixes for better coverage."""
        try:
            prefix_int = int(prefix, 16)
        except ValueError:
            return

        neighbor_deltas = [-1, 1, -16, 16, -17, 17, -15, 15, -256, 256, -4096, 4096]
        for delta in neighbor_deltas:
            neighbor = prefix_int + delta
            if 0 <= neighbor <= 0xFFFF:
                neighbor_prefix = f"{neighbor:04x}"
                if neighbor_prefix in self._phash_index:
                    candidates.update(self._phash_index[neighbor_prefix])

    def _try_sha256_match(self, amazon: PhotoAsset) -> Optional[ComparisonResult]:
        """Try to find an exact SHA256 match."""
        if amazon.sha256 and amazon.sha256 in self._by_sha256:
            return ComparisonResult(
                amazon_asset=amazon,
                match_type=MatchResult.EXACT,
                matched_icloud_asset=self._by_sha256[amazon.sha256],
                confidence=1.0,
                reason="SHA256 hash match",
            )
        return None

    def _try_perceptual_match(self, amazon: PhotoAsset) -> Optional[ComparisonResult]:
        """Try to find a perceptual hash match from indexed candidates."""
        if amazon.is_video or not amazon.phash:
            return None

        for icloud in self._get_phash_candidates(amazon):
            result = perceptual_match(amazon, icloud, self.perceptual_threshold)
            if result and result.match_type == MatchResult.PERCEPTUAL:
                return result
        return None

    def _get_comparison_candidates(self, amazon: PhotoAsset) -> list[PhotoAsset]:
        """Get candidate assets for comparison using indexes."""
        dim_date_key = (amazon.dimensions, amazon.exif_date[:10] if amazon.exif_date else None)
        if dim_date_key in self._by_dimensions_date:
            return self._by_dimensions_date[dim_date_key]

        if amazon.exif_date:
            date_key = amazon.exif_date[:10]
            if date_key in self._by_date:
                return self._by_date[date_key]

        return self.icloud_assets

    def compare_asset(self, amazon: PhotoAsset) -> ComparisonResult:
        """Compare a single Amazon asset against iCloud library."""
        result = self._try_sha256_match(amazon)
        if result:
            return result

        if self.lazy_phash:
            self._ensure_phash(amazon)

        result = self._try_perceptual_match(amazon)
        if result:
            return result

        candidates = self._get_comparison_candidates(amazon)
        return compare(amazon, candidates, self.perceptual_threshold)

    def compare_all(
        self,
        amazon_assets: list[PhotoAsset],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[ComparisonResult]:
        """Compare all Amazon assets against iCloud library."""
        results = []
        total = len(amazon_assets)

        for i, amazon in enumerate(amazon_assets):
            if self.verbose and i % 100 == 0:
                self._log(f"Comparing {i + 1}/{total}...")

            if progress_callback and i % 10 == 0:
                progress_callback(i, total)

            results.append(self.compare_asset(amazon))

        if progress_callback:
            progress_callback(total, total)

        return results
