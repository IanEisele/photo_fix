"""Report generation and file copying."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import (
    ComparisonResult,
    LivePhotoComparisonResult,
    MatchResult,
    ProcessingStats,
)


class Reporter:
    """Handles report generation and file staging."""

    def __init__(
        self,
        output_folder: Path,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        self.output_folder = Path(output_folder)
        self.dry_run = dry_run
        self.verbose = verbose
        self.stats = ProcessingStats()
        self.log_entries: list[dict] = []

    def _log(self, message: str, level: str = "info") -> None:
        """Log message and store in log entries."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
        }
        self.log_entries.append(entry)

        if self.verbose:
            prefix = {"info": "[INFO]", "warn": "[WARN]", "error": "[ERROR]"}
            print(f"{prefix.get(level, '[LOG]')} {message}")

    def setup_output_folder(self) -> None:
        """Create output folder structure."""
        if self.dry_run:
            self._log(f"[DRY RUN] Would create folder: {self.output_folder}")
            return

        self.output_folder.mkdir(parents=True, exist_ok=True)
        (self.output_folder / "missing").mkdir(exist_ok=True)
        (self.output_folder / "uncertain").mkdir(exist_ok=True)
        self._log(f"Created output folder: {self.output_folder}")

    def copy_missing_file(self, result: ComparisonResult) -> bool:
        """Copy a missing file to the staging folder."""
        src = result.amazon_asset.path
        dest_folder = self.output_folder / "missing"
        dest = dest_folder / src.name

        # Handle filename conflicts
        counter = 1
        while dest.exists():
            stem = src.stem
            suffix = src.suffix
            dest = dest_folder / f"{stem}_{counter}{suffix}"
            counter += 1

        if self.dry_run:
            self._log(f"[DRY RUN] Would copy: {src} -> {dest}")
            return True

        try:
            shutil.copy2(src, dest)
            self._log(f"Copied: {src} -> {dest}")
            return True
        except (IOError, OSError) as e:
            self._log(f"Failed to copy {src}: {e}", level="error")
            self.stats.errors += 1
            return False

    def copy_uncertain_file(self, result: ComparisonResult) -> bool:
        """Copy an uncertain match file for review."""
        src = result.amazon_asset.path
        dest_folder = self.output_folder / "uncertain"
        dest = dest_folder / src.name

        # Handle filename conflicts
        counter = 1
        while dest.exists():
            stem = src.stem
            suffix = src.suffix
            dest = dest_folder / f"{stem}_{counter}{suffix}"
            counter += 1

        if self.dry_run:
            self._log(f"[DRY RUN] Would copy for review: {src} -> {dest}")
            return True

        try:
            shutil.copy2(src, dest)
            self._log(f"Copied for review: {src} -> {dest}")
            return True
        except (IOError, OSError) as e:
            self._log(f"Failed to copy {src}: {e}", level="error")
            self.stats.errors += 1
            return False

    def process_results(
        self,
        results: list[ComparisonResult],
        live_results: list[LivePhotoComparisonResult],
    ) -> None:
        """Process all comparison results and copy files as needed."""
        self.setup_output_folder()

        # Process regular photo results
        for result in results:
            self._update_stats(result)

            if result.is_missing:
                self.copy_missing_file(result)
            elif result.needs_review:
                self.copy_uncertain_file(result)

        # Process Live Photo results
        for live_result in live_results:
            self.stats.live_photos_processed += 1

            # Check image component
            if live_result.image_result.is_missing:
                self.copy_missing_file(live_result.image_result)
                self.stats.missing_files += 1
            elif live_result.image_result.needs_review:
                self.copy_uncertain_file(live_result.image_result)

            # Check video component
            if live_result.video_result:
                if live_result.video_result.is_missing:
                    self.copy_missing_file(live_result.video_result)
                    self.stats.missing_files += 1
                elif live_result.video_result.needs_review:
                    self.copy_uncertain_file(live_result.video_result)

    def _update_stats(self, result: ComparisonResult) -> None:
        """Update statistics based on comparison result."""
        match result.match_type:
            case MatchResult.EXACT:
                self.stats.exact_matches += 1
            case MatchResult.PERCEPTUAL:
                self.stats.perceptual_matches += 1
            case MatchResult.METADATA:
                self.stats.metadata_matches += 1
            case MatchResult.UNCERTAIN:
                self.stats.uncertain_matches += 1
            case MatchResult.NO_MATCH:
                self.stats.missing_files += 1

    def _result_to_dict(self, result: ComparisonResult) -> dict:
        """Convert a ComparisonResult to a dictionary for JSON."""
        data = {
            "amazon_path": str(result.amazon_asset.path),
            "match_type": result.match_type.value,
            "confidence": result.confidence,
            "reason": result.reason,
        }

        if result.matched_icloud_asset:
            data["matched_icloud_path"] = str(result.matched_icloud_asset.path)

        return data

    def _live_result_to_dict(self, result: LivePhotoComparisonResult) -> dict:
        """Convert a LivePhotoComparisonResult to a dictionary for JSON."""
        data = {
            "amazon_image_path": str(result.amazon_live_photo.image_asset.path),
            "image_result": self._result_to_dict(result.image_result),
        }

        if result.amazon_live_photo.video_asset:
            data["amazon_video_path"] = str(result.amazon_live_photo.video_asset.path)

        if result.video_result:
            data["video_result"] = self._result_to_dict(result.video_result)

        return data

    def generate_report(
        self,
        results: list[ComparisonResult],
        live_results: list[LivePhotoComparisonResult],
        year: int,
        amazon_folder: Path,
    ) -> Path:
        """Generate the final JSON report."""
        # Collect missing files
        missing = [
            self._result_to_dict(r) for r in results if r.match_type == MatchResult.NO_MATCH
        ]

        # Collect uncertain matches
        uncertain = [
            self._result_to_dict(r)
            for r in results
            if r.match_type == MatchResult.UNCERTAIN
        ]

        # Collect Live Photo issues
        live_photo_missing = []
        live_photo_uncertain = []
        for lr in live_results:
            if lr.image_result.is_missing or (
                lr.video_result and lr.video_result.is_missing
            ):
                live_photo_missing.append(self._live_result_to_dict(lr))
            elif lr.image_result.needs_review or (
                lr.video_result and lr.video_result.needs_review
            ):
                live_photo_uncertain.append(self._live_result_to_dict(lr))

        report = {
            "generated_at": datetime.now().isoformat(),
            "year": year,
            "amazon_folder": str(amazon_folder),
            "dry_run": self.dry_run,
            "summary": self.stats.to_dict(),
            "missing_files": missing,
            "uncertain_matches": uncertain,
            "live_photo_missing": live_photo_missing,
            "live_photo_uncertain": live_photo_uncertain,
            "processing_log": self.log_entries,
        }

        report_path = self.output_folder / "report.json"

        if self.dry_run:
            self._log(f"[DRY RUN] Would write report to: {report_path}")
            # Still write the report even in dry-run mode for review
            report_path.parent.mkdir(parents=True, exist_ok=True)

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        self._log(f"Report written to: {report_path}")
        return report_path

    def print_summary(self) -> None:
        """Print a summary of the processing results."""
        print("\n" + "=" * 50)
        print("PROCESSING SUMMARY")
        print("=" * 50)
        print(f"Total Amazon files:     {self.stats.total_amazon_files}")
        print(f"Total iCloud files:     {self.stats.total_icloud_files}")
        print("-" * 50)
        print(f"Exact matches:          {self.stats.exact_matches}")
        print(f"Perceptual matches:     {self.stats.perceptual_matches}")
        print(f"Metadata matches:       {self.stats.metadata_matches}")
        print(f"Uncertain (review):     {self.stats.uncertain_matches}")
        print(f"Missing (to restore):   {self.stats.missing_files}")
        print("-" * 50)
        print(f"Live Photos processed:  {self.stats.live_photos_processed}")
        print(f"Errors:                 {self.stats.errors}")
        print("=" * 50)

        if self.dry_run:
            print("\n[DRY RUN] No files were actually copied.")
