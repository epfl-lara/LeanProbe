"""Backwards-compatible facade for LeanProbe internals.

The implementation lives in focused modules (:mod:`segmentation`,
:mod:`projects`, :mod:`errors`, :mod:`payloads`, :mod:`sessions`,
:mod:`probe`). This module re-exports the historically public names so that
``from lean_probe.core import segment_file`` and similar imports keep working.
"""

from __future__ import annotations

from .errors import (  # noqa: F401
    ErrorCode,
    _dead_server_error,
    _error_code_for_exception,
    _error_code_for_message,
    _timeout_error_text,
    _timeout_exception,
    hint_for_code,
)
from .payloads import (  # noqa: F401
    DEFAULT_MESSAGE_LIMIT,
    DEFAULT_SORRY_LIMIT,
    DEFAULT_TACTIC_LIMIT,
    FEEDBACK_ENTRIES_PER_LINE,
    FEEDBACK_GOALS_CHARS,
    FEEDBACK_MESSAGE_CHARS,
    FEEDBACK_TACTIC_LIMIT,
    NOISY_MESSAGE_PREFIXES,
    _clean_message_text,
    _feedback_lean,
    _format_message_summary,
    _has_sorry,
    _message_payloads,
    _pos_to_dict,
    _sorry_payloads,
    _tactic_payloads,
    error_envelope,
    outcome,
)
from .payloads import response_payload as _response_payload  # noqa: F401
from .probe import (  # noqa: F401
    DEFAULT_MAX_CODE_SESSIONS,
    DEFAULT_MAX_FILE_SESSIONS,
    DEFAULT_MAX_SCRATCH_SESSIONS,
    LeanProbe,
    _looks_like_declaration,
)
from .projects import (  # noqa: F401
    LOCAL_REPL_CANDIDATES,
    PROJECT_MARKERS,
    _local_repl_dir,
    find_lean_project_root,
)
from .segmentation import (  # noqa: F401
    DECLARATION_KINDS,
    DECLARATION_MODIFIERS,
    DECLARATION_PATTERN,
    LeanIncrementalSegment,
    _find_segment,
    _line_number,
    _mutual_target_hint,
    _sha,
    segment_file,
)
from .sessions import (  # noqa: F401
    _Checkpoint,
    _CodeSession,
    _import_lean_interact,
    _IncrementalSession,
    build_server,
    run_command,
)

__all__ = [
    "LeanProbe",
    "LeanIncrementalSegment",
    "segment_file",
    "find_lean_project_root",
]
