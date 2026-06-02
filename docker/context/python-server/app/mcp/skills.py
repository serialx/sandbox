"""
Skills loading MCP tools — aligned with Claude Code skill loading mechanism.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.exceptions import ResourceNotFoundException
from app.core.service_container import services

from .app import mcp


logger = logging.getLogger(__name__)


@mcp.tool(output_schema=None, tags={'official'})
async def sandbox_load_skill(name: Optional[str] = None) -> str:
    """Load a skill by name, or list all available skills when name is omitted.

    When called without arguments, returns a plain-text listing of every
    registered skill (name + description).  When called with a skill name,
    returns the SKILL.md body prefixed with ``Base Path: <path>`` so the agent
    can resolve relative paths such as ``./scripts/xxx.py``.

    Args:
        name: Skill name to load. Omit to list all available skills.

    Returns:
        Plain-text skills listing (no name), or Base Path + content (with name).
    """
    from app.services.skills import SkillService

    skills_service: SkillService = services.get('skills_service')

    # --- list mode ---
    if name is None:
        collection = skills_service.list_metadata()
        lines = [f'Skills Count: {len(collection.skills)}', '']
        for skill in collection.skills:
            description = skill.metadata.get('description', '')
            lines.append(f'- {skill.name}')
            lines.append(f'  {description}')
        return '\n'.join(lines)

    # --- load mode ---
    try:
        content_result = skills_service.get_skill_content(name)
    except ResourceNotFoundException:
        return f'Skill not found: {name}'

    return f'Base Path: {content_result.path}\n\n{content_result.content}'
