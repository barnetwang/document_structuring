"""Shared utility functions used across the package."""

import re
import logging

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Convert a heading title into a safe filename component.

    Strips Markdown decorators and replaces unsafe characters.
    """
    clean_name = name.replace('*', '').replace('#', '')
    return re.sub(r'[\\/*?:"<>|]', '', clean_name).replace(' ', '_')


def section_sort_key(section_number: str) -> list[tuple[int, int, str]]:
    """Generate a sort key for dotted section numbers (e.g. '1.2.3').

    Handles mixed numeric/non-numeric parts correctly so that
    '1.2' sorts before '1.10'.
    """
    result = []
    for part in section_number.split('.'):
        if part.isdigit():
            result.append((0, int(part), ''))
        else:
            result.append((1, 0, part))
    return result
