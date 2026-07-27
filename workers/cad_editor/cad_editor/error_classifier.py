from __future__ import annotations

from .contracts import ErrorClassification


REPAIRABLE_CODES = {
    "PYTHON_SYNTAX_ERROR",
    "MISSING_MODEL_PARAMS",
    "INVALID_MODEL_PARAMS",
    "MISSING_BUILD_MODEL",
    "INVALID_BUILD_MODEL",
    "MISSING_CAD_PART",
    "INVALID_CAD_PART_FIELD",
    "NONLITERAL_DECORATOR_ARGUMENT",
    "UNKNOWN_MODEL_PARAMETER",
    "UNKNOWN_DEPENDENCY",
    "DUPLICATE_SEMANTIC_ID",
    "INVALID_DEPENDENCY",
    "FORBIDDEN_CALL",
    "CADQUERY_RUNTIME_ERROR",
    "BUILD_MODEL_RETURN_ERROR",
    "GEOMETRY_BUILD_ERROR",
}
NONREPAIRABLE_CODES = {
    "CANDIDATE_SOURCE_HASH_MISMATCH",
    "SOURCE_HASH_MISMATCH",
    "VALIDATION_TIMEOUT",
    "VALIDATION_WORKER_ERROR",
}


def classify_validation_error(
    validation_result: dict,
    *,
    candidate_path: str,
) -> ErrorClassification:
    diagnostics = validation_result.get("diagnostics", [])
    if not diagnostics:
        return ErrorClassification(
            repairable=False,
            category="unknown",
            stop_reason="Validation failed without structured diagnostics.",
        )
    if validation_result.get("repairable_hint") is False:
        return ErrorClassification(
            repairable=False,
            category=str(validation_result.get("stage") or "unknown"),
            stop_reason="The validator marked this failure as non-repairable.",
        )

    function_names: list[str] = []
    parameter_queries: list[str] = []
    for diagnostic in diagnostics:
        code = str(diagnostic.get("error_code") or "")
        if (
            diagnostic.get("repairable_hint") is False
            or code in NONREPAIRABLE_CODES
            or code not in REPAIRABLE_CODES
        ):
            return ErrorClassification(
                repairable=False,
                category=str(validation_result.get("stage") or "unknown"),
                stop_reason=(
                    f"Validation error {code or 'UNKNOWN'} is not source-repairable."
                ),
            )
        file_path = diagnostic.get("file_path")
        if file_path and str(file_path) != candidate_path:
            return ErrorClassification(
                repairable=False,
                category="scope",
                stop_reason="Validation failed outside the candidate source file.",
            )
        function_name = diagnostic.get("function_name")
        if isinstance(function_name, str) and function_name not in function_names:
            function_names.append(function_name)
        if code == "UNKNOWN_MODEL_PARAMETER":
            for symbol in diagnostic.get("related_symbols", []):
                if (
                    isinstance(symbol, str)
                    and symbol not in {"ModelParams", "params"}
                    and symbol not in parameter_queries
                ):
                    parameter_queries.append(symbol)

    return ErrorClassification(
        repairable=True,
        category=str(validation_result.get("stage") or "validation"),
        related_function_names=function_names,
        related_parameter_queries=parameter_queries,
    )
