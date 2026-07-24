from __future__ import annotations

import ast
from typing import Any


REPORT_SCHEMA_VERSION = 1
REQUIRED_DECORATOR_FIELDS = (
    "id",
    "role",
    "library",
    "editable",
    "protected_regions",
    "parameters",
    "depends_on",
    "consumes_tags",
    "produces_tags",
    "search_keys",
)
STRING_FIELDS = {"id", "role", "library"}
TUPLE_FIELDS = {
    "protected_regions",
    "parameters",
    "depends_on",
    "consumes_tags",
    "produces_tags",
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
    if isinstance(node, ast.Constant) and type(node.value) in {str, bool}:
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
    if not fields:
        errors.append(
            validation_error(
                "model_params_fields",
                "ModelParams must define at least one annotated field.",
                model_params,
            )
        )
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
    if not features:
        errors.append(
            validation_error(
                "cad_feature_missing",
                "At least one public CAD feature function is required.",
            )
        )

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
                        "Boolean, or tuple of literal strings.",
                        keyword.value,
                    )
                )
    return check_result(errors)


def decorator_fields_check(
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
        values["id"]
        for _function, values in parsed
        if type(values.get("id")) is str and values["id"]
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
        if type(values["editable"]) is not bool:
            errors.append(
                validation_error(
                    "decorator_editable",
                    f"{function.name}.editable must be a literal Boolean.",
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

        feature_id = values["id"]
        if type(feature_id) is str and feature_id:
            if feature_id in seen_ids:
                errors.append(
                    validation_error(
                        "duplicate_feature_id",
                        f'Duplicate cad_part id "{feature_id}".',
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
            unknown_dependencies = sorted(set(dependencies) - feature_ids)
            if unknown_dependencies:
                errors.append(
                    validation_error(
                        "unknown_dependencies",
                        f"{function.name} references unknown cad_part ids: "
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


def validate_cad_source(source: str) -> dict:
    checks: dict[str, dict] = {}
    try:
        tree = ast.parse(source, filename="model.py")
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
            "forbidden_calls",
        ):
            checks[name] = skipped_check("Python source could not be parsed.")
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "valid": False,
            "safe_to_execute": False,
            "checks": checks,
        }

    checks["syntax"] = check_result([])
    checks["model_params"], model_fields = model_params_check(tree)
    checks["build_model"] = build_model_check(tree)
    checks["cad_part_decorators"], decorators = decorator_presence_check(tree)
    checks["decorator_fields"] = decorator_fields_check(decorators, model_fields)
    checks["decorator_literals"] = decorator_literal_check(decorators)
    checks["forbidden_calls"] = forbidden_calls_check(tree)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "valid": all(check["passed"] for check in checks.values()),
        "safe_to_execute": checks["syntax"]["passed"]
        and checks["forbidden_calls"]["passed"],
        "checks": checks,
    }
