"""Foundation-level reusable building blocks for personal automation tools."""

from .batch import collect_items, find_common_named_items, group_by_name, iter_paths
from .batch_operations import batch_copy, batch_move, batch_zip, filter_paths
from .filesystem import (
    copy_or_move,
    ensure_unique_destination,
    list_items,
    move_to_garbage,
    read_lines,
)
from runtime.process import run_command
from .power_status import PowerStatus, read_power_status
from settings.user_space import UserSpacePaths, configured_paths, create_configured_directories, paths_for_root
from .path import (
    PathLike,
    absolute_path,
    clean_path,
    get_ancestor,
    get_directory,
    get_filename,
    get_project_root,
    join_paths,
    normalize_path,
    path_entry_exists,
    to_posix,
    to_windows,
)

__all__ = [
    "PathLike",
    "absolute_path",
    "PowerStatus",
    "batch_copy",
    "batch_move",
    "batch_zip",
    "clean_path",
    "collect_items",
    "copy_or_move",
    "configured_paths",
    "create_configured_directories",
    "ensure_unique_destination",
    "filter_paths",
    "find_common_named_items",
    "get_ancestor",
    "get_directory",
    "get_filename",
    "get_project_root",
    "group_by_name",
    "iter_paths",
    "join_paths",
    "list_items",
    "move_to_garbage",
    "normalize_path",
    "path_entry_exists",
    "read_lines",
    "read_power_status",
    "run_command",
    "to_posix",
    "to_windows",
    "paths_for_root",
    "UserSpacePaths",
]
