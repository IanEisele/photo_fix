# Photo Restore Tool

Compare Amazon Photos backup against iCloud Photos export to identify and restore missing files.

## Features

- **Folder-to-folder comparison** - Compare two local photo folders
- **Multiple matching strategies**:
  - SHA256 hash (exact match)
  - Perceptual hash (visually similar images)
  - Metadata matching (date, dimensions, file size)
- **Live Photo support** - Detects and handles image+video pairs
- **HEIC/HEIF support** - Full support for Apple's image format
- **Web UI** - Easy-to-use browser interface with folder browser
- **CLI** - Command-line interface for scripting

## Installation

Requires Python 3.12+. Uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
# Clone the repository
git clone git@github.com:IanEisele/photo_fix.git
cd photo_fix

# Install dependencies
uv sync
```

## Usage

### Web UI (Recommended)

```bash
uv run python web_ui.py
```

Opens a browser at `http://localhost:8080` with:
- Folder browser for selecting Amazon, iCloud, and output folders
- File counts displayed when folders are selected
- Comparison results with statistics
- Export button to copy missing files

### CLI

```bash
# Run comparison
uv run python photo_restore.py \
  --amazon-folder ~/amazon-photos/2023 \
  --icloud-folder ~/icloud-export/2023 \
  --year 2023 \
  --output ~/photo-restore \
  --dry-run

# Run without --dry-run to copy missing files
uv run python photo_restore.py \
  --amazon-folder ~/amazon-photos/2023 \
  --icloud-folder ~/icloud-export/2023 \
  --year 2023 \
  --output ~/photo-restore
```

#### CLI Options

| Option | Description |
|--------|-------------|
| `--amazon-folder` | Path to Amazon Photos backup folder |
| `--icloud-folder` | Path to iCloud Photos export folder |
| `--year` | Year being processed (for report metadata) |
| `--output` | Output folder for missing files and report |
| `--dry-run` | Preview only, don't copy files |
| `--verbose`, `-v` | Enable verbose output |
| `--perceptual-threshold` | Hamming distance threshold (default: 5, lower = stricter) |

## How It Works

1. **Scan folders** - Recursively finds all photos and videos
2. **Compute hashes** - SHA256 for exact matching, perceptual hash for visual similarity
3. **Compare** - Tries matching strategies in priority order:
   - Exact SHA256 match (100% confidence)
   - Perceptual hash match (high confidence)
   - Metadata match (date + dimensions + size)
4. **Report** - Generates JSON report with results
5. **Export** - Copies missing files to staging folder

## Output

- `{output}/missing/` - Files not found in iCloud
- `{output}/uncertain/` - Files needing manual review
- `{output}/report.json` - Detailed comparison results

## Project Structure

```
photo_fix/
├── pyproject.toml      # Dependencies
├── photo_restore.py    # CLI entry point
├── web_ui.py           # NiceGUI web interface
├── models.py           # Data classes
├── amazon_reader.py    # Amazon folder scanner
├── icloud_reader.py    # iCloud folder scanner
├── comparators.py      # Hash and metadata comparison
├── live_photos.py      # Live Photo handling
└── reporter.py         # Report generation
```

## Dependencies

- [Pillow](https://pillow.readthedocs.io/) - Image processing
- [pillow-heif](https://github.com/bigcat88/pillow_heif) - HEIC/HEIF support
- [imagehash](https://github.com/JohannesBuchner/imagehash) - Perceptual hashing
- [python-dateutil](https://dateutil.readthedocs.io/) - Date parsing
- [NiceGUI](https://nicegui.io/) - Web UI framework

## License

MIT
