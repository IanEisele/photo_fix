"""iCloud export folder scanner."""

from photo_restore.readers.base import BaseReader


class ICloudReader(BaseReader):
    """Scanner for iCloud Photos export folder."""

    @property
    def log_prefix(self) -> str:
        return "iCloud"
