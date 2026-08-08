from __future__ import annotations

import ast
import re
from typing import Any


REPORT_SCHEMA_VERSION = 2
REQUIRED_DECORATOR_FIELDS = (
    "semantic_id",
    "role",
    "library",
    "parameters",
    "depends_on",
    "search_keys",
)
STRING_FIELDS = {"semantic_id", "role", "library"}
TUPLE_FIELDS = {
    "parameters",
    "depends_on",
    "search_keys",
}
ALLOWED_IMPORT_ROOTS = {"cadquery_runtime", "dataclasses", "math", "typing"}
FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "getattr",
    "setattr",
    "importlib.import_module",
    "cq.exporters.export",
    "cadquery.exporters.export",
}
FORBIDDEN_CALL_PREFIXES = (
    "os.",
    "subprocess.",
    "shutil.",
    "socket.",
    "requests.",
    "httpx.",
    "urllib.",
)
FORBIDDEN_METHOD_NAMES = {
    "open",
    "read",
    "read_text",
    "read_bytes",
    "write",
    "writelines",
    "write_text",
    "write_bytes",
    "unlink",
    "remove",
    "rmdir",
    "mkdir",
    "makedirs",
    "rename",
    "replace",
    "touch",
}
REFERENCE_ERROR_CODES = {
    "build_model_dependency_mismatch",
    "build_model_feature_missing",
    "cyclic_dependency",
    "duplicate_dependency",
    "duplicate_semantic_id",
    "parameter_metadata_mismatch",
    "self_dependency",
    "unknown_dependencies",
    "unknown_parameters",
}
ERROR_CODE_MAP = {
    "syntax_error": "PYTHON_SYNTAX_ERROR",
    "model_params_count": "MISSING_MODEL_PARAMS",
    "model_params_dataclass": "INVALID_MODEL_PARAMS",
    "model_params_fields": "INVALID_MODEL_PARAMS",
    "build_model_count": "MISSING_BUILD_MODEL",
    "build_model_signature": "INVALID_BUILD_MODEL",
    "cad_feature_missing": "MISSING_CAD_PART",
    "cad_part_decorator_count": "MISSING_CAD_PART",
    "decorator_fields": "INVALID_CAD_PART_FIELD",
    "decorator_string_field": "INVALID_CAD_PART_FIELD",
    "decorator_tuple_field": "INVALID_CAD_PART_FIELD",
    "decorator_library": "INVALID_CAD_PART_FIELD",
    "search_keys_empty": "INVALID_CAD_PART_FIELD",
    "decorator_positional_argument": "NONLITERAL_DECORATOR_ARGUMENT",
    "decorator_unpacking": "NONLITERAL_DECORATOR_ARGUMENT",
    "decorator_nonliteral": "NONLITERAL_DECORATOR_ARGUMENT",
    "unknown_parameters": "UNKNOWN_MODEL_PARAMETER",
    "unknown_dependencies": "UNKNOWN_DEPENDENCY",
    "duplicate_dependency": "INVALID_DEPENDENCY",
    "cyclic_dependency": "INVALID_DEPENDENCY",
    "build_model_dependency_mismatch": "INVALID_DEPENDENCY",
    "build_model_feature_missing": "INVALID_DEPENDENCY",
    "parameter_metadata_mismatch": "PARAMETER_METADATA_MISMATCH",
    "duplicate_semantic_id": "DUPLICATE_SEMANTIC_ID",
    "self_dependency": "INVALID_DEPENDENCY",
    "forbidden_import": "IMPORT_ERROR",
    "forbidden_call": "FORBIDDEN_CALL",
}


def validation_error(code: str, message: str, node: ast.AST | None = None) -> dict:
    error = {"code": code, "message": message}
    if node is not None and hasattr(node, "lineno"):
        error["line"] = node.lineno
        error["column"] = node.col_offset + 1
    return error


def check_result(errors: list[dict], *, skipped: bool = False) -> dict:
    return {
        "passed": not errors and not skipped,
        "skipped": skipped,
        "errors": errors,
    }


def skipped_check(reason: str) -> dict:
    return check_result(
        [validation_error("check_skipped", reason)],
        skipped=True,
    )


def decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.Tuple):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or type(item.value) is not str:
                raise ValueError("tuple values must be literal strings")
            values.append(item.value)
        return tuple(values)
    raise ValueError("value is not an allowed literal")


def model_params_check(tree: ast.Module) -> tuple[dict, set[str]]:
    errors: list[dict] = []
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
    ]
    if len(classes) != 1:
        errors.append(
            validation_error(
                "model_params_count",
                f"Expected exactly one top-level ModelParams class; found {len(classes)}.",
            )
        )
        return check_result(errors), set()

    model_params = classes[0]
    valid_dataclass = False
    for decorator in model_params.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if decorator_name(decorator.func) != "dataclass" or decorator.args:
            continue
        if len(decorator.keywords) != 1:
            continue
        keyword = decorator.keywords[0]
        if (
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
        ):
            valid_dataclass = True
            break
    if not valid_dataclass:
        errors.append(
            validation_error(
                "model_params_dataclass",
                "ModelParams must use @dataclass(frozen=True).",
                model_params,
            )
        )

    fields = {
        node.target.id
        for node in model_params.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    return check_result(errors), fields


def build_model_check(tree: ast.Module) -> dict:
    errors: list[dict] = []
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "build_model"
    ]
    if len(functions) != 1:
        errors.append(
            validation_error(
                "build_model_count",
                f"Expected exactly one top-level build_model function; found {len(functions)}.",
            )
        )
        return check_result(errors)

    function = functions[0]
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    valid_signature = (
        isinstance(function, ast.FunctionDef)
        and len(positional) == 1
        and positional[0].arg == "params"
        and isinstance(positional[0].annotation, ast.Name)
        and positional[0].annotation.id == "ModelParams"
        and not arguments.vararg
        and not arguments.kwarg
        and not arguments.kwonlyargs
        and not arguments.defaults
    )
    if not valid_signature:
        errors.append(
            validation_error(
                "build_model_signature",
                "build_model must be synchronous and have the exact signature "
                "build_model(params: ModelParams).",
                function,
            )
        )
    return check_result(errors)


def public_features(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name != "build_model"
        and not node.name.startswith("_")
    ]


def cad_part_decorators(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    return [
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and decorator_name(decorator.func) == "cad_part"
    ]


def decorator_presence_check(tree: ast.Module) -> tuple[dict, list[tuple[ast.AST, ast.Call]]]:
    errors: list[dict] = []
    decorators: list[tuple[ast.AST, ast.Call]] = []
    features = public_features(tree)

    for function in features:
        matches = cad_part_decorators(function)
        if len(matches) != 1:
            errors.append(
                validation_error(
                    "cad_part_decorator_count",
                    f"{function.name} must have exactly one @cad_part(...) decorator.",
                    function,
                )
            )
            continue
        decorators.append((function, matches[0]))
    return check_result(errors), decorators


def decorator_literal_check(decorators: list[tuple[ast.AST, ast.Call]]) -> dict:
    errors: list[dict] = []
    for function, decorator in decorators:
        if decorator.args:
            errors.append(
                validation_error(
                    "decorator_positional_argument",
                    f"{function.name} must not use positional @cad_part arguments.",
                    decorator,
                )
            )
        for keyword in decorator.keywords:
            if keyword.arg is None:
                errors.append(
                    validation_error(
                        "decorator_unpacking",
                        f"{function.name} must not unpack @cad_part arguments.",
                        keyword.value,
                    )
                )
                continue
            try:
                literal_value(keyword.value)
            except ValueError:
                errors.append(
                    validation_error(
                        "decorator_nonliteral",
                        f"{function.name}.{keyword.arg} must be a literal string, "
                        "or tuple of literal strings.",
                        keyword.value,
                    )
                )
    return check_result(errors)


def effective_parameter_references(
    tree: ast.Module,
) -> dict[str, set[str]]:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    direct = {
        name: {
            node.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "params"
        }
        for name, function in functions.items()
    }
    calls = {
        name: {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
            and node.func.id.startswith("_")
        }
        for name, function in functions.items()
    }
    memo: dict[str, set[str]] = {}

    def resolve(name: str, visiting: set[str]) -> set[str]:
        if name in memo:
            return memo[name]
        if name in visiting:
            return set(direct.get(name, set()))
        values = set(direct.get(name, set()))
        for helper in calls.get(name, set()):
            values.update(resolve(helper, visiting | {name}))
        memo[name] = values
        return values

    return {name: resolve(name, set()) for name in functions}


def build_model_dependency_data(
    tree: ast.Module,
    parsed: list[tuple[ast.AST, dict[str, Any]]],
) -> tuple[dict[str, set[str]], set[str]]:
    build_model = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "build_model"
        ),
        None,
    )
    function_to_semantic = {
        function.name: str(values["semantic_id"])
        for function, values in parsed
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    observed = {
        semantic_id: set()
        for semantic_id in function_to_semantic.values()
    }
    invoked: set[str] = set()
    produced_by: dict[str, str] = {}
    if build_model is None:
        return observed, invoked

    def process(call: ast.Call, assignment: str | None) -> None:
        if not isinstance(call.func, ast.Name):
            return
        semantic_id = function_to_semantic.get(call.func.id)
        if semantic_id is None:
            return
        invoked.add(semantic_id)
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            if isinstance(argument, ast.Name) and argument.id in produced_by:
                observed[semantic_id].add(produced_by[argument.id])
        if assignment:
            produced_by[assignment] = semantic_id

    for statement in build_model.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            process(statement.value, statement.targets[0].id)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            process(statement.value, statement.target.id)
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            process(statement.value, None)
        elif isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):
            process(statement.value, None)
    return observed, invoked


def decorator_fields_check(
    tree: ast.Module,
    decorators: list[tuple[ast.AST, ast.Call]],
    model_fields: set[str],
) -> dict:
    errors: list[dict] = []
    parsed: list[tuple[ast.AST, dict[str, Any]]] = []

    for function, decorator in decorators:
        actual_fields = tuple(keyword.arg for keyword in decorator.keywords)
        if actual_fields != REQUIRED_DECORATOR_FIELDS:
            errors.append(
                validation_error(
                    "decorator_fields",
                    f"{function.name} must use the required @cad_part fields in exact order: "
                    f"{', '.join(REQUIRED_DECORATOR_FIELDS)}.",
                    decorator,
                )
            )
            continue

        values: dict[str, Any] = {}
        literal = True
        for keyword in decorator.keywords:
            try:
                values[keyword.arg] = literal_value(keyword.value)
            except ValueError:
                literal = False
        if literal:
            parsed.append((function, values))

    feature_ids = {
        values["semantic_id"]
        for _function, values in parsed
        if type(values.get("semantic_id")) is str and values["semantic_id"]
    }
    seen_ids: set[str] = set()
    for function, values in parsed:
        for field in STRING_FIELDS:
            value = values[field]
            if type(value) is not str or not value.strip():
                errors.append(
                    validation_error(
                        "decorator_string_field",
                        f"{function.name}.{field} must be a non-empty literal string.",
                        function,
                    )
                )
        if values["library"] != "cadquery":
            errors.append(
                validation_error(
                    "decorator_library",
                    f'{function.name}.library must be "cadquery".',
                    function,
                )
            )
        for field in TUPLE_FIELDS:
            value = values[field]
            if type(value) is not tuple or any(
                type(item) is not str or not item.strip() for item in value
            ):
                errors.append(
                    validation_error(
                        "decorator_tuple_field",
                        f"{function.name}.{field} must be a tuple of non-empty literal strings.",
                        function,
                    )
                )

        feature_id = values["semantic_id"]
        if type(feature_id) is str and feature_id:
            if feature_id in seen_ids:
                errors.append(
                    validation_error(
                        "duplicate_semantic_id",
                        f'Duplicate cad_part semantic_id "{feature_id}".',
                        function,
                    )
                )
            seen_ids.add(feature_id)

        parameters = values["parameters"]
        if type(parameters) is tuple:
            unknown_parameters = sorted(set(parameters) - model_fields)
            if unknown_parameters:
                errors.append(
                    validation_error(
                        "unknown_parameters",
                        f"{function.name} references unknown ModelParams fields: "
                        f"{', '.join(unknown_parameters)}.",
                        function,
                    )
                )

        dependencies = values["depends_on"]
        if type(dependencies) is tuple:
            if len(dependencies) != len(set(dependencies)):
                errors.append(
                    validation_error(
                        "duplicate_dependency",
                        f"{function.name}.depends_on must not contain duplicates.",
                        function,
                    )
                )
            unknown_dependencies = sorted(set(dependencies) - feature_ids)
            if unknown_dependencies:
                errors.append(
                    validation_error(
                        "unknown_dependencies",
                        f"{function.name} references unknown cad_part semantic_ids: "
                        f"{', '.join(unknown_dependencies)}.",
                        function,
                    )
                )
            if feature_id in dependencies:
                errors.append(
                    validation_error(
                        "self_dependency",
                        f"{function.name} must not depend on itself.",
                        function,
                    )
                )

        if type(values["search_keys"]) is tuple and not values["search_keys"]:
            errors.append(
                validation_error(
                    "search_keys_empty",
                    f"{function.name}.search_keys must contain at least one string.",
                    function,
                )
            )

    dependency_graph = {
        str(values["semantic_id"]): list(values["depends_on"])
        for _function, values in parsed
        if isinstance(values.get("semantic_id"), str)
        and isinstance(values.get("depends_on"), tuple)
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(semantic_id: str, path: list[str]) -> None:
        if semantic_id in visiting:
            cycle_start = path.index(semantic_id)
            cycle = path[cycle_start:] + [semantic_id]
            errors.append(
                validation_error(
                    "cyclic_dependency",
                    "CAD feature dependencies must be acyclic: "
                    + " → ".join(cycle),
                )
            )
            return
        if semantic_id in visited:
            return
        visiting.add(semantic_id)
        for dependency in dependency_graph.get(semantic_id, []):
            if dependency in dependency_graph:
                visit(dependency, [*path, semantic_id])
        visiting.remove(semantic_id)
        visited.add(semantic_id)

    for semantic_id in dependency_graph:
        visit(semantic_id, [])

    references = effective_parameter_references(tree)
    for function, values in parsed:
        declared = set(values.get("parameters", ()))
        inferred = references.get(function.name, set())
        missing = sorted(inferred - declared)
        stale = sorted(declared - inferred)
        if missing or stale:
            errors.append(
                validation_error(
                    "parameter_metadata_mismatch",
                    f"{function.name}.parameters must match effective ModelParams usage; "
                    f"missing={missing}, stale={stale}.",
                    function,
                )
            )

    observed_dependencies, invoked = build_model_dependency_data(tree, parsed)
    all_feature_ids = set(dependency_graph)
    missing_invocations = sorted(all_feature_ids - invoked)
    if missing_invocations:
        errors.append(
            validation_error(
                "build_model_feature_missing",
                "build_model must invoke every cad_part feature: "
                + ", ".join(missing_invocations),
            )
        )
    for function, values in parsed:
        semantic_id = str(values.get("semantic_id"))
        declared = set(values.get("depends_on", ()))
        observed = observed_dependencies.get(semantic_id, set())
        if declared != observed:
            errors.append(
                validation_error(
                    "build_model_dependency_mismatch",
                    f"{semantic_id}.depends_on must match direct build_model dataflow; "
                    f"declared={sorted(declared)}, observed={sorted(observed)}.",
                    function,
                )
            )
    return check_result(errors)


def import_aliases_and_errors(tree: ast.Module) -> tuple[dict[str, str], list[dict]]:
    aliases: dict[str, str] = {}
    errors: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                aliases[alias.asname or root] = alias.name
                if root not in ALLOWED_IMPORT_ROOTS:
                    errors.append(
                        validation_error(
                            "forbidden_import",
                            f'Importing "{alias.name}" is forbidden.',
                            node,
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if node.level or root not in ALLOWED_IMPORT_ROOTS:
                errors.append(
                    validation_error(
                        "forbidden_import",
                        f'Importing from "{module or "."}" is forbidden.',
                        node,
                    )
                )
            for alias in node.names:
                aliases[alias.asname or alias.name] = (
                    f"{module}.{alias.name}" if module else alias.name
                )
    return aliases, errors


def resolved_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = resolved_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def forbidden_calls_check(tree: ast.Module) -> dict:
    aliases, errors = import_aliases_and_errors(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = resolved_name(node.func, aliases)
        if not name:
            continue
        method_name = name.rsplit(".", 1)[-1]
        forbidden = (
            name in FORBIDDEN_CALLS
            or name.endswith(".exporters.export")
            or name.startswith(FORBIDDEN_CALL_PREFIXES)
            or method_name in FORBIDDEN_METHOD_NAMES
        )
        if forbidden:
            errors.append(
                validation_error(
                    "forbidden_call",
                    f'Calling "{name}" is forbidden.',
                    node,
                )
            )
    return check_result(errors)


def parameter_references_check(
    tree: ast.Module,
    model_fields: set[str],
) -> dict:
    errors: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "params"
            and node.attr not in model_fields
        ):
            continue
        key = (node.attr, node.lineno, node.col_offset)
        if key in seen:
            continue
        seen.add(key)
        errors.append(
            validation_error(
                "unknown_parameters",
                f'ModelParams has no field named "{node.attr}".',
                node,
            )
        )
    return check_result(errors)


def semantic_id_for_function(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    for decorator in cad_part_decorators(function):
        for keyword in decorator.keywords:
            if (
                keyword.arg == "semantic_id"
                and isinstance(keyword.value, ast.Constant)
                and type(keyword.value.value) is str
            ):
                return keyword.value.value
    return None


def symbol_at_line(tree: ast.Module, line: int) -> tuple[str | None, str | None]:
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min(
            [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
        )
        end = node.end_lineno or node.lineno
        if start <= line <= end:
            return node.name, semantic_id_for_function(node)
    return None, None


def related_symbols(error: dict) -> list[str]:
    message = str(error.get("message", ""))
    quoted = re.findall(r'["\']([^"\']+)["\']', message)
    symbols: list[str] = []
    for value in quoted:
        if value not in symbols:
            symbols.append(value)
    return symbols[:8]


def normalized_diagnostics(
    errors: list[dict],
    *,
    stage: str,
    file_path: str,
    tree: ast.Module | None,
) -> list[dict]:
    diagnostics: list[dict] = []
    for error in errors:
        line = error.get("line")
        function_name = None
        semantic_id = None
        if tree is not None and isinstance(line, int) and line > 0:
            function_name, semantic_id = symbol_at_line(tree, line)
        diagnostic = {
            "error_code": ERROR_CODE_MAP.get(
                str(error.get("code")),
                str(error.get("code", "VALIDATION_ERROR")).upper(),
            ),
            "message": str(error.get("message", "CAD validation failed.")),
            "stage": stage,
            "file_path": file_path,
            "related_symbols": related_symbols(error),
        }
        if isinstance(line, int):
            diagnostic["line"] = line
        column = error.get("column")
        if isinstance(column, int):
            diagnostic["column"] = column
        if function_name is not None:
            diagnostic["function_name"] = function_name
        if semantic_id is not None:
            diagnostic["semantic_id"] = semantic_id
        diagnostics.append(diagnostic)
    return diagnostics


def failed_report(
    *,
    stage: str,
    checks: dict[str, dict],
    diagnostics: list[dict],
) -> dict:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "failed",
        "stage": stage,
        "repairable_hint": True,
        "diagnostics": diagnostics,
        "build_artifacts": None,
        "valid": False,
        "safe_to_execute": False,
        "checks": checks,
    }


def validate_cad_source(source: str, file_path: str = "model.py") -> dict:
    checks: dict[str, dict] = {}
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        syntax_error = {
            "code": "syntax_error",
            "message": exc.msg,
            "line": exc.lineno or 0,
            "column": exc.offset or 0,
        }
        checks["syntax"] = check_result([syntax_error])
        for name in (
            "model_params",
            "build_model",
            "cad_part_decorators",
            "decorator_fields",
            "decorator_literals",
            "parameter_references",
            "forbidden_calls",
        ):
            checks[name] = skipped_check("Python source could not be parsed.")
        return failed_report(
            stage="syntax",
            checks=checks,
            diagnostics=normalized_diagnostics(
                [syntax_error],
                stage="syntax",
                file_path=file_path,
                tree=None,
            ),
        )

    checks["syntax"] = check_result([])
    checks["model_params"], model_fields = model_params_check(tree)
    checks["build_model"] = build_model_check(tree)
    checks["cad_part_decorators"], decorators = decorator_presence_check(tree)
    contract_errors = [
        *checks["model_params"]["errors"],
        *checks["build_model"]["errors"],
        *checks["cad_part_decorators"]["errors"],
    ]
    if contract_errors:
        for name in (
            "decorator_fields",
            "decorator_literals",
            "parameter_references",
            "forbidden_calls",
        ):
            checks[name] = skipped_check(
                "Runtime contract validation did not pass."
            )
        return failed_report(
            stage="runtime_contract",
            checks=checks,
            diagnostics=normalized_diagnostics(
                contract_errors,
                stage="runtime_contract",
                file_path=file_path,
                tree=tree,
            ),
        )

    checks["decorator_fields"] = decorator_fields_check(
        tree,
        decorators,
        model_fields,
    )
    checks["decorator_literals"] = decorator_literal_check(decorators)
    decorator_errors = [
        error
        for error in checks["decorator_fields"]["errors"]
        if error.get("code") not in REFERENCE_ERROR_CODES
    ]
    decorator_errors.extend(checks["decorator_literals"]["errors"])
    if decorator_errors:
        checks["parameter_references"] = skipped_check(
            "Decorator validation did not pass."
        )
        checks["forbidden_calls"] = skipped_check(
            "Decorator validation did not pass."
        )
        return failed_report(
            stage="decorator_validation",
            checks=checks,
            diagnostics=normalized_diagnostics(
                decorator_errors,
                stage="decorator_validation",
                file_path=file_path,
                tree=tree,
            ),
        )

    checks["forbidden_calls"] = forbidden_calls_check(tree)
    if checks["forbidden_calls"]["errors"]:
        checks["parameter_references"] = skipped_check(
            "Security validation did not pass."
        )
        return failed_report(
            stage="security",
            checks=checks,
            diagnostics=normalized_diagnostics(
                checks["forbidden_calls"]["errors"],
                stage="security",
                file_path=file_path,
                tree=tree,
            ),
        )

    checks["parameter_references"] = parameter_references_check(
        tree,
        model_fields,
    )
    reference_errors = list(checks["parameter_references"]["errors"])
    reference_errors.extend(
        [
        error
        for error in checks["decorator_fields"]["errors"]
        if error.get("code") in REFERENCE_ERROR_CODES
        ]
    )
    if reference_errors:
        return failed_report(
            stage="reference_validation",
            checks=checks,
            diagnostics=normalized_diagnostics(
                reference_errors,
                stage="reference_validation",
                file_path=file_path,
                tree=tree,
            ),
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed",
        "stage": "static",
        "repairable_hint": False,
        "diagnostics": [],
        "build_artifacts": None,
        "valid": True,
        "safe_to_execute": True,
        "checks": checks,
    }
