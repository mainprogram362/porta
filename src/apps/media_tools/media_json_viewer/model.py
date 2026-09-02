"""Compatibility exports for the retired standalone JSON viewer."""

from apps.media_tools.media_information.browsing import (
    MAXIMUM_MATCH_CANDIDATES,
    VIDEO_SUFFIXES,
    FilterRule,
    ViewerRecord,
    WorkspaceRecord,
    collect_path_candidates,
    is_video_path,
    load_viewer_records,
    load_workspace_records,
    match_paths,
    path_candidates,
    record_matches_rules,
    record_search_text,
)

__all__ = [
    "MAXIMUM_MATCH_CANDIDATES",
    "VIDEO_SUFFIXES",
    "FilterRule",
    "ViewerRecord",
    "WorkspaceRecord",
    "collect_path_candidates",
    "is_video_path",
    "load_viewer_records",
    "load_workspace_records",
    "match_paths",
    "path_candidates",
    "record_matches_rules",
    "record_search_text",
]
