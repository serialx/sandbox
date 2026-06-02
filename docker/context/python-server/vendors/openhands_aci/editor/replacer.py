"""
String replacement utilities for the editor.

This module provides flexible string replacement with multiple modes.
"""

import difflib
from typing import Literal, TypedDict

ReplaceMode = Literal['ALL', 'FIRST', 'LAST']


class ReplacementResult(TypedDict):
    """Result of a string replacement operation."""

    new_content: str
    replacements: int
    diff_text: str


def unified_diff(old_text: str, new_text: str) -> str:
    """
    Generate unified diff between old and new text.

    Args:
        old_text: Original text
        new_text: Modified text

    Returns:
        Unified diff string
    """
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile='old',
        tofile='new',
        n=3,
        lineterm='',
    )
    return '\n'.join(diff)


def do_replacement(
    content: str,
    old_str: str,
    new_str: str,
    mode: ReplaceMode,
) -> ReplacementResult:
    """
    Execute string replacement based on specified mode.

    Args:
        content: Original content
        old_str: String to replace
        new_str: Replacement string
        mode: Replacement mode ('ALL', 'FIRST', 'LAST')

    Returns:
        ReplacementResult containing new_content, replacements count, and diff_text

    Raises:
        ValueError: If mode is invalid
    """
    if mode == 'ALL':
        new_content = content.replace(old_str, new_str)
        replacements = content.count(old_str)
    elif mode == 'FIRST':
        new_content = content.replace(old_str, new_str, 1)
        replacements = 1 if old_str in content else 0
    elif mode == 'LAST':
        last_index = content.rfind(old_str)
        if last_index != -1:
            new_content = (
                content[:last_index] + new_str + content[last_index + len(old_str) :]
            )
            replacements = 1
        else:
            new_content = content
            replacements = 0
    else:
        raise ValueError(f"Invalid mode: {mode}. Must be one of: 'ALL', 'FIRST', 'LAST'")

    return {
        'new_content': new_content,
        'replacements': replacements,
        'diff_text': unified_diff(content, new_content),
    }
