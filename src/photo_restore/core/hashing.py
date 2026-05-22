"""Hashing and metadata extraction utilities."""

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import imagehash
import pillow_heif
from PIL import Image
from PIL.ExifTags import IFD, TAGS

logger = logging.getLogger(__name__)

# Register HEIF/HEIC support with Pillow
pillow_heif.register_heif_opener()


def compute_sha256(path: Path) -> Optional[str]:
    """Compute SHA256 hash of a file."""
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        logger.warning(f"Failed to compute SHA256 for {path}: {e}")
        return None


def compute_phash(path: Path) -> Optional[str]:
    """Compute perceptual hash of an image."""
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception as e:
        logger.warning(f"Failed to compute phash for {path}: {e}")
        return None


def get_image_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Get image dimensions."""
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def get_exif_date(path: Path) -> Optional[str]:
    """Extract EXIF date from image - handles both JPEG and HEIC/HEIF."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None

            # Check EXIF IFD for DateTimeOriginal first (most accurate)
            exif_ifd = exif.get_ifd(IFD.Exif)
            if exif_ifd:
                for tag_id, value in exif_ifd.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ("DateTimeOriginal", "DateTimeDigitized"):
                        try:
                            dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                            return dt.isoformat()
                        except ValueError:
                            pass

            # Fall back to base EXIF DateTime
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "DateTime":
                    try:
                        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        return dt.isoformat()
                    except ValueError:
                        pass
    except Exception:
        pass
    return None


def get_file_date(path: Path) -> Optional[str]:
    """Get file modification date as fallback."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        return None
