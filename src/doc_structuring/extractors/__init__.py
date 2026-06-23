"""Document extractor registry.

Use ``get_extractor(ext)`` to obtain an extractor for a file extension.
New formats are registered via the ``@register`` decorator.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import DocumentExtractor

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[DocumentExtractor]] = {}


def register(ext: str):
    """Class decorator: register an extractor for the given file extension.

    Example::

        @register(".pptx")
        class PptxExtractor:
            def extract_lines(self, file_path: str) -> list[tuple[int, str]]:
                ...
    """
    def decorator(cls):
        key = ext.lower()
        if key in _REGISTRY:
            logger.warning("Overwriting extractor for '%s': %s -> %s", key, _REGISTRY[key], cls)
        _REGISTRY[key] = cls
        return cls
    return decorator


def get_extractor(ext: str) -> DocumentExtractor:
    """Return an instance of the extractor registered for *ext*.

    Raises:
        ValueError: If no extractor is registered for the extension.
    """
    cls = _REGISTRY.get(ext.lower())
    if cls is None:
        supported = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported file format: '{ext}'. Supported: {supported}"
        )
    return cls()


def supported_extensions() -> list[str]:
    """Return a sorted list of registered file extensions."""
    return sorted(_REGISTRY)


# Auto-register built-in extractors on import
from . import pdf as _pdf_mod      # noqa: F401, E402
from . import docx as _docx_mod    # noqa: F401, E402
