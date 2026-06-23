"""Application configuration with environment variable fallback."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    """Centralised configuration for all doc_structuring modules.

    Attributes:
        base_dir: Root directory for output and database files.
                  Defaults to the ``DOC_STRUCTURING_BASE_DIR`` environment
                  variable, falling back to the current working directory.
    """

    base_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("DOC_STRUCTURING_BASE_DIR", str(Path.cwd()))
        )
    )

    @property
    def output_dir(self) -> Path:
        """Directory for generated document output trees."""
        return self.base_dir / "output"

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.base_dir / "documents.db"

    @property
    def chunks_subdir(self) -> str:
        """Subdirectory name for chunk files within each document output."""
        return "chunks"
