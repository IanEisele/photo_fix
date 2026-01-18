#!/usr/bin/env python3
"""
Photo Restore Tool - CLI Entry Point

Compare Amazon Photos backup against iCloud Photos export
and identify missing files for restoration.
"""

import argparse
import os
import sys
from pathlib import Path

from tqdm import tqdm

from amazon_reader import AmazonReader
from comparators import PhotoComparator
from icloud_reader import ICloudReader
from live_photos import LivePhotoHandler
from reporter import Reporter


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Compare Amazon Photos backup against iCloud Photos export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run for 2023
  uv run python photo_restore.py \\
    --amazon-folder ~/amazon-photos/2023 \\
    --icloud-folder ~/icloud-export/2023 \\
    --year 2023 \\
    --output ~/photo-restore \\
    --dry-run

  # Actual run
  uv run python photo_restore.py \\
    --amazon-folder ~/amazon-photos/2023 \\
    --icloud-folder ~/icloud-export/2023 \\
    --year 2023 \\
    --output ~/photo-restore
        """,
    )

    parser.add_argument(
        "--amazon-folder",
        type=Path,
        required=True,
        help="Path to Amazon Photos backup folder",
    )
    parser.add_argument(
        "--icloud-folder",
        type=Path,
        required=True,
        help="Path to iCloud Photos export folder",
    )
    parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Year being processed (for report metadata)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output folder for missing files and report",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log all operations without copying files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--perceptual-threshold",
        type=int,
        default=5,
        help="Hamming distance threshold for perceptual matching (default: 5)",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Validate input folders exist
    if not args.amazon_folder.exists():
        print(f"Error: Amazon folder does not exist: {args.amazon_folder}")
        return 1

    if not args.amazon_folder.is_dir():
        print(f"Error: Amazon folder is not a directory: {args.amazon_folder}")
        return 1

    if not args.icloud_folder.exists():
        print(f"Error: iCloud folder does not exist: {args.icloud_folder}")
        return 1

    if not args.icloud_folder.is_dir():
        print(f"Error: iCloud folder is not a directory: {args.icloud_folder}")
        return 1

    print("Photo Restore Tool")
    print("==================")
    print(f"Amazon folder: {args.amazon_folder}")
    print(f"iCloud folder: {args.icloud_folder}")
    print(f"Year: {args.year}")
    print(f"Output: {args.output}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Initialize components
    reporter = Reporter(args.output, dry_run=args.dry_run, verbose=args.verbose)

    try:
        cpu_count = os.cpu_count() or 4

        # Load Amazon photos
        print("Loading Amazon Photos...")
        amazon_reader = AmazonReader(args.amazon_folder, verbose=args.verbose)
        amazon_photos, amazon_live_photos = amazon_reader.load_all()
        reporter.stats.total_amazon_files = len(amazon_photos)
        print(f"  Found {len(amazon_photos)} photos/videos")
        print(f"  Found {len(amazon_live_photos)} Live Photo pairs")

        # Compute hashes for Amazon photos using parallel processing
        print(f"\nComputing SHA256 hashes for Amazon photos ({cpu_count} CPU cores)...")
        with tqdm(total=len(amazon_photos), unit="file", desc="Amazon SHA256") as pbar:
            def amazon_progress(completed: int, total: int, msg: str) -> None:
                pbar.update(completed - pbar.n)

            amazon_reader.compute_hashes_parallel(
                amazon_photos,
                progress_callback=amazon_progress,
                compute_phash=False,  # Lazy phash
            )

        # Load iCloud photos
        print("\nLoading iCloud Photos...")
        icloud_reader = ICloudReader(args.icloud_folder, verbose=args.verbose)
        icloud_photos, icloud_live_photos = icloud_reader.load_all()
        reporter.stats.total_icloud_files = len(icloud_photos)
        print(f"  Found {len(icloud_photos)} photos/videos")
        print(f"  Found {len(icloud_live_photos)} Live Photo pairs")

        # Compute hashes for iCloud photos (need phash for index)
        print(f"\nComputing hashes for iCloud photos ({cpu_count} CPU cores)...")
        with tqdm(total=len(icloud_photos), unit="file", desc="iCloud hashes") as pbar:
            def icloud_progress(completed: int, total: int, msg: str) -> None:
                pbar.update(completed - pbar.n)

            icloud_reader.compute_hashes_parallel(
                icloud_photos,
                progress_callback=icloud_progress,
                compute_phash=True,  # Need phash for index
            )

        # Compare photos with lazy perceptual hashing
        print("\nComparing photos (with lazy perceptual hashing)...")
        lazy_phash_count = {"count": 0}

        def on_lazy_phash(msg: str) -> None:
            lazy_phash_count["count"] += 1

        comparator = PhotoComparator(
            icloud_photos,
            perceptual_threshold=args.perceptual_threshold,
            verbose=args.verbose,
            lazy_phash=True,
            phash_callback=on_lazy_phash,
        )

        with tqdm(total=len(amazon_photos), unit="file", desc="Comparing") as pbar:
            def compare_progress(completed: int, total: int) -> None:
                pbar.update(completed - pbar.n)

            results = comparator.compare_all(amazon_photos, progress_callback=compare_progress)

        if lazy_phash_count["count"] > 0:
            print(f"  Computed {lazy_phash_count['count']} lazy perceptual hashes")

        # Compare Live Photos
        print("\nComparing Live Photos...")
        live_handler = LivePhotoHandler(comparator, verbose=args.verbose)
        live_results = live_handler.compare_all(amazon_live_photos)

        # Process results and copy files
        print("\nProcessing results...")
        reporter.process_results(results, live_results)

        # Generate report
        print("\nGenerating report...")
        report_path = reporter.generate_report(
            results, live_results, args.year, args.amazon_folder
        )
        print(f"Report saved to: {report_path}")

        # Print summary
        reporter.print_summary()

        return 0

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        return 130
    except Exception as e:
        print(f"\nError: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
