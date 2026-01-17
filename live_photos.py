"""Live Photo pairing and comparison logic."""

from typing import Optional

from comparators import PhotoComparator
from models import ComparisonResult, LivePhoto, LivePhotoComparisonResult, MatchResult


class LivePhotoHandler:
    """Handler for Live Photo comparison."""

    def __init__(self, comparator: PhotoComparator, verbose: bool = False):
        self.comparator = comparator
        self.verbose = verbose

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[LivePhoto] {message}")

    def compare_live_photo(self, amazon_live: LivePhoto) -> LivePhotoComparisonResult:
        """
        Compare a Live Photo from Amazon against iCloud library.

        Checks both image and video components.
        """
        # Compare image component
        image_result = self.comparator.compare_asset(amazon_live.image_asset)

        # Compare video component if present
        video_result = None
        if amazon_live.video_asset:
            video_result = self.comparator.compare_asset(amazon_live.video_asset)

        return LivePhotoComparisonResult(
            amazon_live_photo=amazon_live,
            image_result=image_result,
            video_result=video_result,
        )

    def compare_all(
        self, amazon_live_photos: list[LivePhoto]
    ) -> list[LivePhotoComparisonResult]:
        """Compare all Amazon Live Photos against iCloud library."""
        results = []
        total = len(amazon_live_photos)

        for i, live_photo in enumerate(amazon_live_photos):
            if self.verbose and i % 50 == 0:
                self._log(f"Comparing Live Photo {i+1}/{total}...")

            result = self.compare_live_photo(live_photo)
            results.append(result)

        return results

    def get_missing_components(
        self, results: list[LivePhotoComparisonResult]
    ) -> tuple[list[ComparisonResult], list[ComparisonResult]]:
        """
        Get lists of missing image and video components.

        Returns:
            Tuple of (missing_images, missing_videos)
        """
        missing_images = []
        missing_videos = []

        for result in results:
            if result.image_result.is_missing:
                missing_images.append(result.image_result)

            if result.video_result and result.video_result.is_missing:
                missing_videos.append(result.video_result)

        return missing_images, missing_videos

    def get_uncertain_matches(
        self, results: list[LivePhotoComparisonResult]
    ) -> list[LivePhotoComparisonResult]:
        """Get Live Photos with uncertain matches that need review."""
        uncertain = []

        for result in results:
            if result.image_result.needs_review:
                uncertain.append(result)
            elif result.video_result and result.video_result.needs_review:
                uncertain.append(result)

        return uncertain
