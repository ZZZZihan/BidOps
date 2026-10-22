"""Read-only verification of caller-pinned evaluation artifacts and run metadata.

The manifest digest is an external trust anchor: this module never creates a
manifest, calculates an acceptable replacement pin, or authenticates a timestamp.
The caller must retain the pin before running the evaluation. Passing these checks
establishes artifact consistency, not real-model quality or human acceptance.
"""

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from bidops.models import StrictModel

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]
CodeSha = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{40}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Cost = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]+(?:\.[0-9]+)?$")]


class FreezeModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class FrozenFile(FreezeModel):
    path: Annotated[str, StringConstraints(strict=True, min_length=1)]
    sha256: Digest

    @field_validator("path")
    @classmethod
    def relative_canonical_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            value != value.strip()
            or "\x00" in value
            or "\\" in value
            or PureWindowsPath(value).drive
            or path.is_absolute()
            or ".." in path.parts
            or value == "."
            or path.as_posix() != value
        ):
            raise ValueError("path must be a canonical relative POSIX file path")
        return value


class FreezeManifest(FreezeModel):
    protocol_id: Identifier
    protocol_version: Identifier
    frozen_at: AwareDatetime
    files: list[FrozenFile] = Field(min_length=1)


class RunVersions(FreezeModel):
    code_sha: CodeSha
    data_sha256: Digest
    profile_sha256: Digest
    prompt_version: Identifier
    scorer_version: Identifier
    # Required even for fixtures: null records the absence of an actual model.
    actual_model_id: Identifier | None
    execution_kind: Literal["scorer_fixture", "recorded_model_output"]
    model_calls: Count
    input_tokens: Count
    output_tokens: Count
    estimated_cost_cny: Cost
    parameters: dict[str, JsonValue]

    @field_validator("parameters")
    @classmethod
    def finite_json_parameters(cls, value: dict) -> dict:
        numeric = {
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "timeout_seconds",
        }
        integer = {"max_tokens", "max_output_tokens", "seed"}
        choices = {
            "thinking": {"enabled", "disabled"},
            "response_format": {"text", "json_object"},
        }
        for key, setting in value.items():
            if key in numeric:
                valid = type(setting) is int or (type(setting) is float and math.isfinite(setting))
                if key == "timeout_seconds":
                    valid = valid and setting > 0
            elif key in integer:
                valid = type(setting) is int
                if key != "seed":
                    valid = valid and setting > 0
            elif key in choices:
                valid = isinstance(setting, str) and setting in choices[key]
            else:
                valid = False
            if not valid:
                raise ValueError("parameters require known finite scalar inference settings")
        return value

    @model_validator(mode="after")
    def execution_matches_usage(self) -> Self:
        if self.execution_kind == "scorer_fixture":
            if (
                self.actual_model_id is not None
                or self.model_calls != 0
                or self.input_tokens != 0
                or self.output_tokens != 0
                or Decimal(self.estimated_cost_cny) != 0
                or self.parameters
            ):
                raise ValueError(
                    "scorer_fixture requires null actual model, empty parameters "
                    "and zero usage/cost"
                )
        elif self.actual_model_id is None or self.model_calls == 0:
            raise ValueError("recorded_model_output requires an actual model and at least one call")
        return self


def _issue(code: str, *identifiers: str) -> dict:
    return {"code": code, "ids": list(identifiers), "status": "FAIL"}


def _result(issues: list[dict], **details) -> dict:
    return {
        "status": "FAIL" if issues else "PASS",
        "issues": issues,
        "failure_ids": sorted({identifier for item in issues for identifier in item["ids"]}),
        **details,
    }


def _validation_ids(error: ValidationError) -> list[str]:
    return [".".join(map(str, item["loc"])) or "record" for item in error.errors()]


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def verify_freeze(
    root: str | Path, manifest_path: str | Path, expected_manifest_sha256: str
) -> dict:
    """Verify the exact pinned manifest bytes, then every declared file under root.

    A relative manifest path is resolved from root. Absolute paths are accepted
    only inside root. Symlinks may point inside root, but cannot escape it or
    introduce duplicate aliases of a declared file. No file is written.
    """
    counts = {"expected_files": 0, "verified_files": 0}
    try:
        TypeAdapter(Digest).validate_python(expected_manifest_sha256)
    except ValidationError:
        return _result([_issue("invalid_manifest_pin", "expected_manifest_sha256")], counts=counts)
    try:
        boundary = Path(root).resolve(strict=True)
        if not boundary.is_dir():
            raise ValueError("root must be a directory")
    except (OSError, ValueError, RuntimeError, TypeError):
        return _result([_issue("invalid_freeze_root", "root")], counts=counts)

    try:
        manifest = Path(manifest_path)
        if not manifest.is_absolute():
            manifest = boundary / manifest
        manifest = manifest.resolve(strict=True)
        if not manifest.is_relative_to(boundary):
            return _result([_issue("manifest_path_outside_root", "manifest")], counts=counts)
        manifest_bytes = manifest.read_bytes()
    except (OSError, ValueError, RuntimeError, TypeError):
        return _result([_issue("manifest_unreadable", "manifest")], counts=counts)

    observed_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    metadata = {"manifest_sha256": observed_sha256, "counts": counts}
    if observed_sha256 != expected_manifest_sha256:
        # Never trust or parse replacement content after a pin mismatch.
        return _result([_issue("manifest_sha256_mismatch", "manifest")], **metadata)
    try:
        payload = json.loads(manifest_bytes, object_pairs_hook=_unique_json_object)
        frozen = FreezeManifest.model_validate(payload)
    except (UnicodeError, ValueError, RecursionError) as error:
        ids = _validation_ids(error) if isinstance(error, ValidationError) else ["manifest"]
        return _result([_issue("invalid_manifest", *ids)], **metadata)

    counts["expected_files"] = len(frozen.files)
    issues = []
    seen_paths = set()
    seen_targets = set()
    for entry in frozen.files:
        if entry.path in seen_paths:
            issues.append(_issue("duplicate_frozen_path", entry.path))
            continue
        seen_paths.add(entry.path)
        try:
            target = (boundary / entry.path).resolve(strict=False)
            if not target.is_relative_to(boundary):
                issues.append(_issue("frozen_path_outside_root", entry.path))
                continue
            if target in seen_targets:
                issues.append(_issue("duplicate_frozen_target", entry.path))
                continue
            seen_targets.add(target)
            if not target.is_file():
                issues.append(_issue("frozen_file_missing", entry.path))
                continue
            with target.open("rb") as stream:
                observed_file_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        except (OSError, ValueError, RuntimeError):
            issues.append(_issue("frozen_file_unreadable", entry.path))
            continue
        if observed_file_sha256 != entry.sha256:
            issues.append(_issue("frozen_file_sha256_mismatch", entry.path))
        else:
            counts["verified_files"] += 1
    return _result(
        issues,
        protocol_id=frozen.protocol_id,
        protocol_version=frozen.protocol_version,
        frozen_at=frozen.frozen_at.isoformat(),
        **metadata,
    )


def verify_run_versions(expected: RunVersions | dict, observed: RunVersions | dict) -> dict:
    """Compare complete metadata from actual execution against a fixed expectation.

    The caller supplies actual provider-returned model identity, parameters and
    usage; a requested model alias is not a substitute. No provider is contacted.
    These checks cannot authenticate self-reported or fabricated usage records.
    """
    values = {}
    issues = []
    for label, value in (("expected", expected), ("observed", observed)):
        try:
            values[label] = RunVersions.model_validate(value).model_dump(mode="json")
        except ValidationError as error:
            issues.append(
                _issue("invalid_run_versions", *(f"{label}.{i}" for i in _validation_ids(error)))
            )
    if issues:
        return _result(issues, counts={"compared_fields": 0})
    for field in RunVersions.model_fields:
        left, right = values["expected"][field], values["observed"][field]
        if field == "estimated_cost_cny":
            matches = Decimal(left) == Decimal(right)
        elif field == "parameters":
            # JSON distinguishes true from 1, unlike Python container equality.
            matches = json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
        else:
            matches = left == right
        if not matches:
            issues.append(_issue("run_version_mismatch", field))
    return _result(issues, counts={"compared_fields": len(RunVersions.model_fields)})
