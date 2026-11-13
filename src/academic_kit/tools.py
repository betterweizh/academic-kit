"""Find the installed tools and start them.

A tool joins the launcher by declaring an entry point in the
``academic_kit.tools`` group of its own ``pyproject.toml``. The entry
point's name is the button label and its value is the module that
``python -m`` runs::

    [project.entry-points.'academic_kit.tools']
    figconvert = 'figconvert'

The tool's description comes from its package summary. Tools never
import the launcher, and each one runs in its own process, so a tool
that fails cannot affect the launcher or any other tool.
"""

import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata

__all__ = [
    'ENTRY_POINT_GROUP',
    'Tool',
    'discover_tools',
    'launch_tool',
]

ENTRY_POINT_GROUP = 'academic_kit.tools'


@dataclass(frozen=True, slots=True, kw_only=True)
class Tool:
    """One tool the launcher can start.

    Attributes
    ----------
    name : str
        Button label, the entry point's name.
    module : str
        Module run with ``python -m``.
    description : str
        One-line summary from the tool's package metadata; empty if the
        package has none.
    """

    name: str
    module: str
    description: str


def discover_tools() -> list[Tool]:
    """List the installed tools, sorted by name.

    Returns
    -------
    list of Tool
        One entry per entry point in the ``academic_kit.tools`` group.

    Raises
    ------
    ValueError
        If an entry point names an attribute (``module:attr``) instead
        of a module, since only a module can be run with ``python -m``.
    """
    tools = [
        _tool_from_entry_point(entry_point)
        for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP)
    ]
    return sorted(tools, key=_sort_key)


def launch_tool(tool: Tool) -> subprocess.Popen[bytes]:
    """Start a tool in its own process with the launcher's Python.

    Parameters
    ----------
    tool : Tool
        Tool to start.

    Returns
    -------
    subprocess.Popen
        The running tool. The launcher does not wait for it.

    Raises
    ------
    OSError
        If the process cannot be started.
    """
    return subprocess.Popen(
        [
            sys.executable,
            '-m',
            tool.module,
        ]
    )


def _sort_key(tool: Tool) -> str:
    """Return the case-insensitive key that orders tools by name."""
    return (
        tool.name
        .casefold()
    )


def _tool_from_entry_point(entry_point: metadata.EntryPoint) -> Tool:
    """Build a tool from its entry point.

    Parameters
    ----------
    entry_point : importlib.metadata.EntryPoint
        Entry point in the ``academic_kit.tools`` group.

    Returns
    -------
    Tool
        The tool it describes.

    Raises
    ------
    ValueError
        If the entry point value names an attribute instead of a module.
    """
    if entry_point.attr:
        raise ValueError(
            f'tool entry point must name a module, not an attribute: '
            f'{entry_point.name!r} = {entry_point.value!r}'
        )
    description = ''
    if entry_point.dist is not None:
        description = entry_point.dist.metadata['Summary'] or ''
    return Tool(
        name=entry_point.name,
        module=entry_point.module,
        description=description,
    )
