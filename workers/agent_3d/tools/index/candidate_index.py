from __future__ import annotations

import ast
import logging
from typing import Any

from workers.indexer.indexer.extractor import extract_part_index
from workers.indexer.indexer.models import IndexingError, SourceFile

from ...failures import WorkflowFailure
from ..base import ToolRepository
from ..hashing import source_hash


LOGGER = logging.getLogger(__name__)

# Tuple-valued @cad_part fields, kept in the record as lists so a lenient scan
# and the project extractor produce the same JSON shape.
_TUPLE_DECORATOR_FIELDS = ("parameters", "depends_on", "search_keys")
_STRING_DECORATOR_FIELDS = ("semantic_id", "role", "library")
_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _literal_string(node: ast.AST | None) -> str:
    """Return a string literal's value, or ``""`` for anything else."""

    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _literal_strings(node: ast.AST | None) -> list[str]:
    """Return the string literals in a tuple or list node, skipping the rest."""

    if not isinstance(node, (ast.Tuple, ast.List)):
        return []
    return [
        item.value
        for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _cad_part_decorator(function: _FunctionNode) -> ast.Call | None:
    """Return the function's ``@cad_part(...)`` decorator call, if it has one."""

    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call) and (
            isinstance(decorator.func, ast.Name) and decorator.func.id == "cad_part"
        ):
            return decorator
    return None


def _segment(source: str, node: ast.AST | None) -> str:
    """Return a node's exact source text, or ``""`` when it has none."""

    if node is None:
        return ""
    segment = ast.get_source_segment(source, node)
    return segment.strip() if segment is not None else ""


def _function_record(node: _FunctionNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
    }


def _build_model_segment(source: str, record: dict[str, Any] | None) -> str:
    """Return ``build_model``'s exact source text, or ``""`` when there is none.

    Sliced from the same bytes the record was derived from, so the text and
    the feature roster always describe one version of the part.
    """

    build_model = (record or {}).get("build_model")
    if not isinstance(build_model, dict):
        return ""
    start = build_model.get("line_start")
    end = build_model.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return ""
    return "\n".join(source.splitlines()[start - 1 : end])


def lenient_part_index(
    source: str,
    *,
    part_id: str,
    part_name: str,
    storage_path: str,
    content_hash: str,
) -> dict[str, Any] | None:
    """Build a part record from source the project extractor would reject.

    The extractor is a contract check as much as a reader: it refuses a part
    whose ``build_model`` does not yet invoke every feature, whose
    ``depends_on`` does not match observed dataflow, or whose decorator fields
    are out of order. Those are the *normal* intermediate states of a candidate
    mid-step -- ``create_feature`` writes a feature and a separate
    ``edit_cad_build_model`` call wires it in, so the source is briefly
    non-conforming by design.

    Refusing to describe the part at all in those windows would make a feature
    that demonstrably exists in the candidate unreadable, which is the very
    failure this whole mechanism exists to remove. So this reads what is
    actually there and declines to judge it: absent or non-literal decorator
    values become empty rather than an error.

    Returns ``None`` only when the source will not parse, where an empty
    roster and no roster mean the same thing anyway.

    The record matches :func:`extract_part_index`'s shape field for field, so
    no consumer needs to know which of the two produced it. It omits only
    ``parameter_references``, which is inferred metadata no consumer here
    reads, and ``metadata_warnings``, which is a conformance report this scan
    deliberately does not produce.
    """

    try:
        tree = ast.parse(source, filename=storage_path)
    except (SyntaxError, ValueError, RecursionError):
        return None

    model_params: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ModelParams":
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            model_params.append(
                {
                    "name": item.target.id,
                    "type_name": _segment(source, item.annotation),
                    "default_source": _segment(source, item.value),
                    "line_start": item.lineno,
                    "line_end": item.end_lineno or item.lineno,
                }
            )
        break

    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    cad_parts: list[dict[str, Any]] = []
    for function in functions:
        decorator = _cad_part_decorator(function)
        if decorator is None:
            continue
        keywords = {
            keyword.arg: keyword.value for keyword in decorator.keywords if keyword.arg
        }
        record: dict[str, Any] = {
            field: _literal_string(keywords.get(field)) for field in _STRING_DECORATOR_FIELDS
        }
        record.update(
            {field: _literal_strings(keywords.get(field)) for field in _TUPLE_DECORATOR_FIELDS}
        )
        record.update(
            {
                "function_name": function.name,
                "decorator_line_start": decorator.lineno,
                "function_line_start": function.lineno,
                "function_line_end": function.end_lineno or function.lineno,
            }
        )
        cad_parts.append(record)

    build_model = next((node for node in functions if node.name == "build_model"), None)
    return {
        "part_id": part_id,
        "part_name": part_name,
        "file_path": storage_path,
        "content_hash": content_hash,
        "model_params": model_params,
        "functions": [_function_record(function) for function in functions],
        "cad_parts": cad_parts,
        "build_model": _function_record(build_model) if build_model is not None else None,
        "metadata_warnings": [],
    }


class CandidatePartIndex:
    """The current editable part's semantic representation, derived from its candidate.

    The persisted project index describes *accepted* source, so it cannot
    contain anything an in-flight edit job created -- a feature built in an
    earlier plan step of this same job is simply absent from it. Since each
    plan step runs its own reasoning chain, a later step that cannot read what
    an earlier step built has no way to learn about it at all. This class is
    what closes that gap: it derives the same part record the project indexer
    would produce, but from the live candidate, so the candidate itself can
    serve as durable memory between steps.

    Freshness is defined by source identity and nothing else -- never elapsed
    time, step number, file modification time, or an assumption that indexing
    happened recently. The invariant is absolute: a returned record was
    derived from exactly the bytes whose hash is :attr:`source_sha256`. When
    the candidate moves, the record is re-derived rather than reused, so a
    record built from superseded bytes is never served as if it described the
    current candidate.

    Re-derivation is a pure ``ast.parse`` -- no execution, no network, no
    model call -- which is what makes verifying on read affordable rather than
    requiring a re-index after every mutation.

    Not thread-safe: one instance belongs to one agent loop, which is
    single-threaded through its tool calls.
    """

    def __init__(
        self,
        *,
        repository: ToolRepository,
        part_id: str,
        part_name: str,
        candidate_path: str,
    ) -> None:
        """Bind the index to one edit job's candidate source object."""

        self._repository = repository
        self._part_id = part_id
        self._part_name = part_name
        self._candidate_path = candidate_path
        self._source_sha256: str | None = None
        self._part_index: dict[str, Any] | None = None
        self._build_model_source = ""
        # How the cached record was derived: "strict" when the project
        # extractor accepted the source, "lenient" when only the tolerant scan
        # could read it, "none" when neither could. Diagnostic only.
        self._mode = "none"
        # The candidate's hash as of the most recent read, whatever came of it.
        self._candidate_sha256: str | None = None

    @property
    def part_id(self) -> str:
        """The part this index describes."""

        return self._part_id

    @property
    def source_sha256(self) -> str | None:
        """Hash of the candidate source the cached record was derived from.

        ``None`` before the first successful read. Equal to
        :attr:`candidate_sha256` whenever a record is being served, since a
        record is only ever built from the bytes that were just read.
        """

        return self._source_sha256

    @property
    def candidate_sha256(self) -> str | None:
        """Hash of the candidate source as of the most recent read."""

        return self._candidate_sha256

    @property
    def mode(self) -> str:
        """How the cached record was derived: ``strict``, ``lenient``, or ``none``."""

        return self._mode

    def build_model_source(self) -> str:
        """``build_model``'s current text, as of the last derivation.

        The feature roster says which features exist; only this says how they
        are currently composed into the single solid the part returns. The two
        answer different questions, and the roster's ``depends_on`` graph does
        not substitute for this one: a feature can be depended upon, invoked,
        and still contribute nothing to the returned value.

        Reflects whatever :meth:`part_index` or :meth:`refresh` last read, so
        callers wanting it for the current candidate should ask for the record
        first. ``""`` when the part has no ``build_model`` or no record.
        """

        return self._build_model_source

    def part_index(self) -> dict[str, Any] | None:
        """The part record for the current candidate, re-deriving only when it moved.

        Reads the candidate, hashes it, and returns the cached record when that
        hash still matches -- so an unchanged candidate costs one read and no
        parsing, and a changed one is re-derived before anything is returned.

        Returns ``None`` when the candidate cannot be read or will not parse.
        It deliberately does not fall back to a record built from earlier
        bytes: a caller asking about the current candidate is better served by
        "no view" -- which sends it to the accepted project index or to its own
        source scan -- than by a confident answer about source that no longer
        exists.
        """

        try:
            source = self._repository.read_text(self._candidate_path)
        except WorkflowFailure:
            return None

        sha256 = source_hash(source)
        self._candidate_sha256 = sha256
        if sha256 == self._source_sha256:
            return self._part_index
        return self._derive(source, sha256)

    def refresh(self) -> dict[str, Any] | None:
        """Re-derive the record from the candidate unconditionally.

        Called by the orchestrator at a successful step boundary, so the next
        step's opening inventory and its read tools both describe the candidate
        as that step actually left it. Ignores the cached hash, which matters
        when a step passed by repairing source that previously only the
        lenient scan could read.
        """

        try:
            source = self._repository.read_text(self._candidate_path)
        except WorkflowFailure:
            return None
        sha256 = source_hash(source)
        self._candidate_sha256 = sha256
        return self._derive(source, sha256)

    def _derive(self, source: str, sha256: str) -> dict[str, Any] | None:
        """Build and cache the part record for exactly these bytes.

        Prefers the project extractor so a conforming candidate produces
        byte-for-byte what a real reindex would, and falls back to the lenient
        scan of the *same* source rather than to an older record.
        """

        source_file = SourceFile.from_content(
            part_id=self._part_id,
            part_name=self._part_name,
            storage_path=self._candidate_path,
            content=source,
        )
        try:
            derived = extract_part_index(source_file)
            mode = "strict"
        # IndexingError covers every contract violation the extractor reports,
        # including a syntax error it has already wrapped. The rest are raised
        # by ast.parse itself for input the extractor never gets to inspect
        # (null bytes, pathological nesting). A candidate mid-edit is expected
        # to be non-conforming, so none of these may reach the agent loop.
        except (IndexingError, SyntaxError, ValueError, RecursionError) as exc:
            LOGGER.debug(
                "candidate index falling back to lenient scan part_id=%s sha256=%s "
                "error_type=%s error=%s",
                self._part_id,
                sha256,
                type(exc).__name__,
                exc,
            )
            derived = lenient_part_index(
                source,
                part_id=self._part_id,
                part_name=self._part_name,
                storage_path=self._candidate_path,
                content_hash=source_file.content_hash,
            )
            mode = "lenient" if derived is not None else "none"

        # Cached even when nothing could be derived, so source that will not
        # parse is not re-parsed on every read while the agent repairs it.
        self._source_sha256 = sha256
        self._part_index = derived
        self._build_model_source = _build_model_segment(source, derived)
        self._mode = mode
        return derived
