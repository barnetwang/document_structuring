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

_REGISTRY: dict[str, type] = {}
_IMPORT_ERRORS: dict[str, ImportError] = {}
_BUILTINS_LOADED = False


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
            logger.warning(
                "Overwriting extractor for '%s': %s -> %s",
                key,
                _REGISTRY[key],
                cls,
            )
        _REGISTRY[key] = cls
        return cls

    return decorator


def _ensure_builtins() -> None:
    """Lazily import built-in extractors (heavy deps: PyMuPDF, python-docx)."""
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    try:
        from . import pdf as _pdf_mod  # noqa: F401
    except ImportError as exc:
        _IMPORT_ERRORS[".pdf"] = exc
        logger.debug("PDF extractor unavailable: %s", exc)
    try:
        from . import docx as _docx_mod  # noqa: F401
    except ImportError as exc:
        _IMPORT_ERRORS[".docx"] = exc
        logger.debug("DOCX extractor unavailable: %s", exc)


def get_extractor(ext: str) -> DocumentExtractor:
    """Return an instance of the extractor registered for *ext*.

    Raises:
        ValueError: If no extractor is registered for the extension.
        ImportError: If the extractor exists but its dependencies are missing.
    """
    _ensure_builtins()
    key = ext.lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        if key in _IMPORT_ERRORS:
            raise ImportError(
                f"Required dependencies for '{ext}' are missing. "
                f"Original error: {_IMPORT_ERRORS[key]}"
            )
        supported = ", ".join(sorted(_REGISTRY)) or "(none loaded)"
        raise ValueError(
            f"Unsupported file format: '{ext}'. Supported: {supported}"
        )
    return cls()


def supported_extensions() -> list[str]:
    """Return a sorted list of registered file extensions."""
    _ensure_builtins()
    return sorted(_REGISTRY)
