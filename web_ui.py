#!/usr/bin/env python3
"""
Photo Restore Tool - Web UI

NiceGUI-based web interface for comparing photo folders.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from nicegui import ui, app

from amazon_reader import AmazonReader, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ALL_EXTENSIONS
from comparators import PhotoComparator
from icloud_reader import ICloudReader
from live_photos import LivePhotoHandler
from models import ComparisonResult, MatchResult, ProcessingStats
from reporter import Reporter


def count_media_files(folder_path: str) -> dict:
    """Count media files in a folder, returning counts by type."""
    try:
        path = Path(folder_path).expanduser()
        if not path.exists() or not path.is_dir():
            return {"photos": 0, "videos": 0, "live_photos": 0, "total": 0}

        photos = 0
        videos = 0
        files_by_stem: dict[str, set[str]] = {}

        for root, _, filenames in os.walk(path):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                stem = Path(filename).stem.upper()

                if ext in IMAGE_EXTENSIONS:
                    photos += 1
                    if stem not in files_by_stem:
                        files_by_stem[stem] = set()
                    files_by_stem[stem].add("image")
                elif ext in VIDEO_EXTENSIONS:
                    videos += 1
                    if stem not in files_by_stem:
                        files_by_stem[stem] = set()
                    files_by_stem[stem].add("video")

        # Count Live Photos (image + video with same stem)
        live_photos = sum(1 for exts in files_by_stem.values() if "image" in exts and "video" in exts)

        return {
            "photos": photos,
            "videos": videos,
            "live_photos": live_photos,
            "total": photos + videos,
        }
    except Exception:
        return {"photos": 0, "videos": 0, "live_photos": 0, "total": 0}


class FolderBrowser:
    """A folder browser dialog component."""

    def __init__(self, on_select: Callable[[str], None], title: str = "Select Folder"):
        self.on_select = on_select
        self.title = title
        self.current_path = Path.home()
        self.dialog: Optional[ui.dialog] = None
        self.folder_list: Optional[ui.column] = None
        self.path_display: Optional[ui.label] = None

    def get_folders(self, path: Path) -> list[Path]:
        """Get list of folders in the given path."""
        try:
            folders = []
            for item in sorted(path.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    folders.append(item)
            return folders
        except PermissionError:
            return []

    def refresh_folder_list(self):
        """Refresh the folder list display."""
        if not self.folder_list:
            return

        self.folder_list.clear()
        self.path_display.set_text(str(self.current_path))

        with self.folder_list:
            # Parent directory button
            if self.current_path.parent != self.current_path:
                with ui.row().classes("items-center w-full hover:bg-gray-100 cursor-pointer p-2 rounded").on(
                    "click", lambda: self.navigate_to(self.current_path.parent)
                ):
                    ui.icon("folder_open", color="amber").classes("text-lg")
                    ui.label("..").classes("text-sm")

            # Subfolders
            folders = self.get_folders(self.current_path)
            if not folders:
                ui.label("No subfolders").classes("text-grey text-sm italic p-2")
            else:
                for folder in folders:
                    with ui.row().classes(
                        "items-center w-full hover:bg-gray-100 cursor-pointer p-2 rounded"
                    ).on("click", lambda f=folder: self.navigate_to(f)):
                        ui.icon("folder", color="amber").classes("text-lg")
                        ui.label(folder.name).classes("text-sm truncate")

    def navigate_to(self, path: Path):
        """Navigate to a folder."""
        if path.is_dir():
            self.current_path = path
            self.refresh_folder_list()

    def select_current(self):
        """Select the current folder."""
        self.on_select(str(self.current_path))
        self.dialog.close()

    def show(self, initial_path: str = ""):
        """Show the folder browser dialog."""
        if initial_path:
            try:
                path = Path(initial_path).expanduser()
                if path.exists() and path.is_dir():
                    self.current_path = path
            except Exception:
                pass

        with ui.dialog() as self.dialog, ui.card().classes("w-[600px]"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label(self.title).classes("text-h6")
                ui.button(icon="close", on_click=self.dialog.close).props("flat round dense")

            ui.separator()

            # Quick access buttons
            with ui.row().classes("gap-2 flex-wrap"):
                ui.button("Home", on_click=lambda: self.navigate_to(Path.home())).props(
                    "flat dense"
                )
                ui.button("Desktop", on_click=lambda: self.navigate_to(Path.home() / "Desktop")).props(
                    "flat dense"
                )
                ui.button("Documents", on_click=lambda: self.navigate_to(Path.home() / "Documents")).props(
                    "flat dense"
                )
                ui.button("/", on_click=lambda: self.navigate_to(Path("/"))).props(
                    "flat dense"
                )

            # Current path display
            with ui.row().classes("items-center gap-2 w-full bg-gray-50 p-2 rounded"):
                ui.icon("folder_open", color="primary")
                self.path_display = ui.label(str(self.current_path)).classes(
                    "text-sm font-mono truncate"
                )

            # Folder list
            with ui.scroll_area().classes("w-full h-64 border rounded"):
                self.folder_list = ui.column().classes("w-full")

            self.refresh_folder_list()

            ui.separator()

            # Action buttons
            with ui.row().classes("justify-end gap-2 w-full"):
                ui.button("Cancel", on_click=self.dialog.close).props("flat")
                ui.button("Select This Folder", on_click=self.select_current, color="primary")

        self.dialog.open()


class PhotoRestoreApp:
    """Main application state and logic."""

    def __init__(self):
        self.amazon_folder: str = ""
        self.icloud_folder: str = ""
        self.output_folder: str = ""
        self.year: int = datetime.now().year
        self.perceptual_threshold: int = 5

        self.is_running: bool = False
        self.is_exporting: bool = False
        self.comparison_done: bool = False
        self.stats: Optional[ProcessingStats] = None
        self.results: list[ComparisonResult] = []
        self.live_results: list = []  # LivePhotoComparisonResult list
        self.log_messages: list[str] = []

        # Folder counts
        self.amazon_counts: dict = {"photos": 0, "videos": 0, "live_photos": 0, "total": 0}
        self.icloud_counts: dict = {"photos": 0, "videos": 0, "live_photos": 0, "total": 0}

    def log(self, message: str) -> None:
        """Add a log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_messages.append(f"[{timestamp}] {message}")

    def clear_log(self) -> None:
        """Clear log messages."""
        self.log_messages.clear()

    @property
    def missing_count(self) -> int:
        """Count of missing files from last comparison."""
        if not self.results:
            return 0
        return sum(1 for r in self.results if r.match_type == MatchResult.NO_MATCH)


# Global app state
app_state = PhotoRestoreApp()


def create_header():
    """Create the header section."""
    with ui.header().classes("bg-primary"):
        ui.label("Photo Restore Tool").classes("text-h5 text-white")
        ui.space()
        with ui.row().classes("items-center"):
            ui.label("Compare Amazon Photos backup against iCloud export").classes(
                "text-white text-caption"
            )


def create_folder_input_with_browse(
    label: str,
    placeholder: str,
    bind_attr: str,
    count_attr: Optional[str] = None,
    show_counts: bool = False,
) -> tuple[ui.input, Optional[ui.label]]:
    """Create a folder input with browse button and optional file counts."""
    ui.label(label).classes("text-weight-medium")

    with ui.row().classes("w-full items-center gap-2"):
        folder_input = ui.input(
            placeholder=placeholder,
            validation={"Required": lambda v: bool(v)},
        ).classes("flex-grow")
        folder_input.bind_value(app_state, bind_attr)

        # Stats label (shown below input)
        stats_label = None
        if show_counts:
            stats_label = ui.label("").classes("text-caption text-grey")

        def update_counts(path: str):
            if show_counts and stats_label and count_attr:
                counts = count_media_files(path)
                setattr(app_state, count_attr, counts)
                if counts["total"] > 0:
                    stats_label.set_text(
                        f"{counts['photos']} photos, {counts['videos']} videos, {counts['live_photos']} Live Photos"
                    )
                    stats_label.classes(remove="text-grey", add="text-primary")
                else:
                    stats_label.set_text("No media files found")
                    stats_label.classes(remove="text-primary", add="text-grey")

        def on_folder_selected(path: str):
            setattr(app_state, bind_attr, path)
            folder_input.value = path
            update_counts(path)

        def open_browser():
            browser = FolderBrowser(
                on_select=on_folder_selected,
                title=f"Select {label}",
            )
            browser.show(getattr(app_state, bind_attr))

        ui.button(icon="folder_open", on_click=open_browser).props("flat").tooltip("Browse...")

        # Also update counts when input changes manually
        if show_counts:
            folder_input.on("blur", lambda e: update_counts(folder_input.value))

    return folder_input, stats_label


def create_folder_inputs():
    """Create folder input section."""
    with ui.card().classes("w-full"):
        ui.label("Folder Configuration").classes("text-h6")

        with ui.grid(columns=2).classes("w-full gap-4"):
            with ui.column().classes("w-full"):
                create_folder_input_with_browse(
                    "Amazon Photos Folder",
                    "/path/to/amazon-photos/2023",
                    "amazon_folder",
                    count_attr="amazon_counts",
                    show_counts=True,
                )

            with ui.column().classes("w-full"):
                create_folder_input_with_browse(
                    "iCloud Export Folder",
                    "/path/to/icloud-export/2023",
                    "icloud_folder",
                    count_attr="icloud_counts",
                    show_counts=True,
                )

            with ui.column().classes("w-full"):
                create_folder_input_with_browse(
                    "Output Folder",
                    "/path/to/photo-restore",
                    "output_folder",
                    show_counts=False,
                )

            with ui.column().classes("w-full"):
                ui.label("Year").classes("text-weight-medium")
                year_input = ui.number(
                    value=app_state.year,
                    min=1990,
                    max=2030,
                    step=1,
                ).classes("w-full")
                year_input.bind_value(app_state, "year")


def create_options():
    """Create options section."""
    with ui.card().classes("w-full"):
        ui.label("Options").classes("text-h6")

        with ui.row().classes("items-center gap-4"):
            ui.label("Perceptual Threshold:").classes("text-weight-medium")
            threshold_slider = ui.slider(min=1, max=15, step=1, value=5).classes(
                "w-48"
            )
            threshold_slider.bind_value(app_state, "perceptual_threshold")
            threshold_label = ui.label()
            threshold_slider.bind_value_to(threshold_label, "text", lambda v: str(int(v)))
            ui.label("(Lower = stricter matching)").classes("text-caption text-grey")


def create_action_buttons(log_area, results_container, stats_container):
    """Create action buttons."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-4"):
            start_button = ui.button(
                "Start Comparison", on_click=lambda: run_comparison(log_area, results_container, stats_container)
            ).classes("bg-primary text-lg").props("size=lg")
            start_button.bind_enabled_from(app_state, "is_running", lambda v: not v)

            stop_button = ui.button("Stop", color="negative")
            stop_button.bind_visibility_from(app_state, "is_running")

            spinner = ui.spinner(size="lg")
            spinner.bind_visibility_from(app_state, "is_running")


def create_log_section():
    """Create log output section."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Processing Log").classes("text-h6")
            ui.button(
                "Clear",
                on_click=lambda: clear_log(log_area),
                color="grey",
            ).props("flat dense")

        log_area = ui.log(max_lines=100).classes("w-full h-48")
        return log_area


def create_results_section():
    """Create results section."""
    with ui.card().classes("w-full"):
        ui.label("Results Summary").classes("text-h6")

        stats_container = ui.column().classes("w-full")

        ui.separator().classes("my-4")

        ui.label("Missing Files").classes("text-subtitle1 text-weight-medium")
        results_container = ui.column().classes("w-full max-h-64 overflow-auto")

        return results_container, stats_container


def clear_log(log_area):
    """Clear the log area."""
    app_state.clear_log()
    log_area.clear()


async def run_comparison(log_area, results_container, stats_container):
    """Run the photo comparison process."""
    # Validate inputs
    if not app_state.amazon_folder:
        ui.notify("Please enter Amazon folder path", type="negative")
        return
    if not app_state.icloud_folder:
        ui.notify("Please enter iCloud folder path", type="negative")
        return
    if not app_state.output_folder:
        ui.notify("Please enter output folder path", type="negative")
        return

    amazon_path = Path(app_state.amazon_folder).expanduser()
    icloud_path = Path(app_state.icloud_folder).expanduser()
    output_path = Path(app_state.output_folder).expanduser()

    if not amazon_path.exists():
        ui.notify(f"Amazon folder does not exist: {amazon_path}", type="negative")
        return
    if not icloud_path.exists():
        ui.notify(f"iCloud folder does not exist: {icloud_path}", type="negative")
        return

    # Clear previous results
    app_state.results.clear()
    app_state.live_results.clear()
    app_state.stats = None
    app_state.comparison_done = False
    results_container.clear()
    stats_container.clear()
    log_area.clear()

    app_state.is_running = True

    def log(msg: str):
        log_area.push(msg)

    try:
        log("Starting photo comparison...")
        log(f"Amazon folder: {amazon_path}")
        log(f"iCloud folder: {icloud_path}")
        log(f"Output folder: {output_path}")
        log("")

        # Initialize reporter (dry_run=True for comparison, export later)
        reporter = Reporter(output_path, dry_run=True, verbose=True)

        # Load Amazon photos
        log("Loading Amazon Photos...")
        await asyncio.sleep(0.1)  # Allow UI to update
        amazon_reader = AmazonReader(amazon_path, verbose=False)
        amazon_photos, amazon_live_photos = amazon_reader.load_all()
        reporter.stats.total_amazon_files = len(amazon_photos)
        log(f"  Found {len(amazon_photos)} photos/videos")
        log(f"  Found {len(amazon_live_photos)} Live Photo pairs")

        # Compute hashes for Amazon photos
        log("")
        log("Computing hashes for Amazon photos...")
        for i, photo in enumerate(amazon_photos):
            if i % 20 == 0:
                log(f"  Processing {i+1}/{len(amazon_photos)}...")
                await asyncio.sleep(0.01)
            amazon_reader.compute_hashes_for_asset(photo)
        log(f"  Completed {len(amazon_photos)} files")

        # Load iCloud photos
        log("")
        log("Loading iCloud Photos...")
        await asyncio.sleep(0.1)
        icloud_reader = ICloudReader(icloud_path, verbose=False)
        icloud_photos, icloud_live_photos = icloud_reader.load_all()
        reporter.stats.total_icloud_files = len(icloud_photos)
        log(f"  Found {len(icloud_photos)} photos/videos")
        log(f"  Found {len(icloud_live_photos)} Live Photo pairs")

        # Compute hashes for iCloud photos
        log("")
        log("Computing hashes for iCloud photos...")
        for i, photo in enumerate(icloud_photos):
            if i % 20 == 0:
                log(f"  Processing {i+1}/{len(icloud_photos)}...")
                await asyncio.sleep(0.01)
            icloud_reader.compute_hashes_for_asset(photo)
        log(f"  Completed {len(icloud_photos)} files")

        # Compare photos
        log("")
        log("Comparing photos...")
        await asyncio.sleep(0.1)
        comparator = PhotoComparator(
            icloud_photos,
            perceptual_threshold=int(app_state.perceptual_threshold),
            verbose=False,
        )
        results = comparator.compare_all(amazon_photos)
        log(f"  Compared {len(results)} files")

        # Compare Live Photos
        log("")
        log("Comparing Live Photos...")
        live_handler = LivePhotoHandler(comparator, verbose=False)
        live_results = live_handler.compare_all(amazon_live_photos)
        log(f"  Compared {len(live_results)} Live Photo pairs")

        # Process results
        log("")
        log("Processing results...")
        reporter.process_results(results, live_results)

        # Generate report
        log("")
        log("Generating report...")
        report_path = reporter.generate_report(
            results, live_results, int(app_state.year), amazon_path
        )
        log(f"Report saved to: {report_path}")

        # Store results for later export
        app_state.results = results
        app_state.live_results = live_results
        app_state.stats = reporter.stats
        app_state.comparison_done = True

        # Update UI with results
        update_stats_display(stats_container, reporter.stats)
        update_results_display(results_container, results)

        log("")
        log("=" * 50)
        log("COMPARISON COMPLETED")
        if app_state.missing_count > 0:
            log(f"Found {app_state.missing_count} missing files ready for export")
        log("=" * 50)

        ui.notify("Comparison completed!", type="positive")

    except Exception as e:
        log(f"ERROR: {e}")
        ui.notify(f"Error: {e}", type="negative")
        import traceback
        log(traceback.format_exc())

    finally:
        app_state.is_running = False


async def export_missing_files(log_area):
    """Export missing files to the output folder."""
    if not app_state.comparison_done:
        ui.notify("Please run a comparison first", type="warning")
        return

    if app_state.missing_count == 0:
        ui.notify("No missing files to export", type="info")
        return

    output_path = Path(app_state.output_folder).expanduser()

    app_state.is_exporting = True

    def log(msg: str):
        log_area.push(msg)

    try:
        log("")
        log("=" * 50)
        log("EXPORTING MISSING FILES")
        log("=" * 50)
        log(f"Output folder: {output_path}")

        # Create reporter for actual export (dry_run=False)
        reporter = Reporter(output_path, dry_run=False, verbose=True)
        reporter.setup_output_folder()

        # Export missing files from comparison results
        missing_results = [r for r in app_state.results if r.match_type == MatchResult.NO_MATCH]
        exported = 0
        errors = 0

        log(f"Exporting {len(missing_results)} missing files...")
        await asyncio.sleep(0.1)

        for i, result in enumerate(missing_results):
            if i % 10 == 0:
                log(f"  Copying {i+1}/{len(missing_results)}...")
                await asyncio.sleep(0.01)

            if reporter.copy_missing_file(result):
                exported += 1
            else:
                errors += 1

        # Export missing Live Photo components
        live_missing = 0
        for live_result in app_state.live_results:
            if live_result.image_result.is_missing:
                if reporter.copy_missing_file(live_result.image_result):
                    live_missing += 1
            if live_result.video_result and live_result.video_result.is_missing:
                if reporter.copy_missing_file(live_result.video_result):
                    live_missing += 1

        log("")
        log("=" * 50)
        log("EXPORT COMPLETED")
        log(f"  Files exported: {exported}")
        if live_missing > 0:
            log(f"  Live Photo components: {live_missing}")
        if errors > 0:
            log(f"  Errors: {errors}")
        log(f"  Output folder: {output_path / 'missing'}")
        log("=" * 50)

        ui.notify(f"Exported {exported} files to {output_path / 'missing'}", type="positive")

    except Exception as e:
        log(f"ERROR: {e}")
        ui.notify(f"Error: {e}", type="negative")
        import traceback
        log(traceback.format_exc())

    finally:
        app_state.is_exporting = False


def update_stats_display(container, stats: ProcessingStats):
    """Update the stats display."""
    container.clear()

    with container:
        with ui.grid(columns=4).classes("w-full gap-4"):
            with ui.card().classes("bg-blue-50"):
                ui.label("Amazon Files").classes("text-caption text-grey")
                ui.label(str(stats.total_amazon_files)).classes("text-h5")

            with ui.card().classes("bg-green-50"):
                ui.label("iCloud Files").classes("text-caption text-grey")
                ui.label(str(stats.total_icloud_files)).classes("text-h5")

            with ui.card().classes("bg-emerald-50"):
                ui.label("Exact Matches").classes("text-caption text-grey")
                ui.label(str(stats.exact_matches)).classes("text-h5 text-positive")

            with ui.card().classes("bg-teal-50"):
                ui.label("Perceptual Matches").classes("text-caption text-grey")
                ui.label(str(stats.perceptual_matches)).classes("text-h5")

            with ui.card().classes("bg-cyan-50"):
                ui.label("Metadata Matches").classes("text-caption text-grey")
                ui.label(str(stats.metadata_matches)).classes("text-h5")

            with ui.card().classes("bg-amber-50"):
                ui.label("Uncertain").classes("text-caption text-grey")
                ui.label(str(stats.uncertain_matches)).classes("text-h5 text-warning")

            with ui.card().classes("bg-red-50"):
                ui.label("Missing").classes("text-caption text-grey")
                ui.label(str(stats.missing_files)).classes("text-h5 text-negative")

            with ui.card().classes("bg-purple-50"):
                ui.label("Live Photos").classes("text-caption text-grey")
                ui.label(str(stats.live_photos_processed)).classes("text-h5")


def update_results_display(container, results: list[ComparisonResult]):
    """Update the results display with missing files."""
    container.clear()

    missing = [r for r in results if r.match_type == MatchResult.NO_MATCH]
    uncertain = [r for r in results if r.match_type == MatchResult.UNCERTAIN]

    with container:
        if not missing and not uncertain:
            ui.label("No missing or uncertain files found!").classes(
                "text-positive text-lg"
            )
            return

        if missing:
            ui.label(f"Missing Files ({len(missing)})").classes(
                "text-weight-medium text-negative"
            )
            with ui.scroll_area().classes("w-full h-48"):
                for result in missing[:100]:  # Limit display
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("warning", color="negative", size="sm")
                        ui.label(str(result.amazon_asset.path.name)).classes(
                            "text-sm"
                        )

            if len(missing) > 100:
                ui.label(f"... and {len(missing) - 100} more").classes(
                    "text-caption text-grey"
                )

        if uncertain:
            ui.separator().classes("my-2")
            ui.label(f"Uncertain Matches ({len(uncertain)})").classes(
                "text-weight-medium text-warning"
            )
            with ui.scroll_area().classes("w-full h-32"):
                for result in uncertain[:50]:
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("help", color="warning", size="sm")
                        ui.label(str(result.amazon_asset.path.name)).classes(
                            "text-sm"
                        )
                        ui.label(f"({result.confidence:.0%})").classes(
                            "text-caption text-grey"
                        )


@ui.page("/")
def main_page():
    """Main application page."""
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        create_folder_inputs()
        create_options()

        # Create log and results sections first (but they appear lower due to order)
        with ui.card().classes("w-full") as action_card:
            action_row = ui.row().classes("items-center gap-4")

        results_container, stats_container = create_results_section()
        log_area = create_log_section()

        # Now populate the action card
        with action_row:
            start_button = ui.button(
                "Start Comparison",
                on_click=lambda: run_comparison(log_area, results_container, stats_container)
            ).classes("bg-primary text-lg").props("size=lg")
            start_button.bind_enabled_from(app_state, "is_running", lambda v: not v)
            start_button.bind_enabled_from(app_state, "is_exporting", lambda v: not v)

            # Export button - appears after comparison
            export_button = ui.button(
                "Export Missing Files",
                on_click=lambda: export_missing_files(log_area),
                color="positive"
            ).classes("text-lg").props("size=lg")
            export_button.bind_visibility_from(app_state, "comparison_done")
            export_button.bind_enabled_from(app_state, "is_exporting", lambda v: not v)
            export_button.bind_enabled_from(app_state, "is_running", lambda v: not v)

            # Spinner for running state
            spinner = ui.spinner(size="lg")
            spinner.bind_visibility_from(app_state, "is_running")

            # Spinner for export state
            export_spinner = ui.spinner(size="lg", color="positive")
            export_spinner.bind_visibility_from(app_state, "is_exporting")

        # Move action card to be right after options
        action_card.move(target_index=2)


def main():
    """Run the web UI."""
    ui.run(
        title="Photo Restore Tool",
        port=8080,
        reload=False,
        show=True,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
