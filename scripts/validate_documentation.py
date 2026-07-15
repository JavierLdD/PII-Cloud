from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "assets" / "model-manifest.yaml"
MODEL_DOC_PATH = ROOT / "docs" / "ml" / "modelos-y-licencias.md"


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "" or value == []:
        raise ValueError(f"{context}: missing required field {key!r}")
    return value


def _load_manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("model manifest must contain a YAML mapping")
    return data


def validate() -> None:
    manifest = _load_manifest()
    _require(manifest, "schema_version", "manifest")
    verified_at = str(_require(manifest, "verified_at", "manifest"))
    date.fromisoformat(verified_at)
    models = _require(manifest, "models", "manifest")
    if not isinstance(models, list):
        raise ValueError("manifest.models must be a list")

    model_doc = MODEL_DOC_PATH.read_text(encoding="utf-8")
    seen_ids: set[str] = set()
    for index, item in enumerate(models):
        context = f"manifest.models[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be a mapping")
        model_id = str(_require(item, "model_id", context))
        if model_id in seen_ids:
            raise ValueError(f"duplicate model_id: {model_id}")
        seen_ids.add(model_id)
        for key in (
            "purpose",
            "consumers",
            "revision_status",
            "declared_license",
            "license_scope",
            "license_source",
        ):
            _require(item, key, context)
        if not str(item["license_source"]).startswith("https://"):
            raise ValueError(f"{context}.license_source must be HTTPS")
        if str(item["declared_license"]).casefold() == "unknown":
            raise ValueError(f"{context}: named upstream models need a license")
        if model_id not in model_doc:
            raise ValueError(f"model missing from modelos-y-licencias.md: {model_id}")

    runtime_artifacts = manifest.get("runtime_model_artifacts", [])
    if not isinstance(runtime_artifacts, list):
        raise ValueError("manifest.runtime_model_artifacts must be a list")
    for index, item in enumerate(runtime_artifacts):
        context = f"manifest.runtime_model_artifacts[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be a mapping")
        for key in (
            "artifact_id",
            "consumer",
            "artifact_source",
            "identity_status",
            "deployed_revision",
            "deployed_license",
            "expected_upstream_model_id",
            "verification_required",
        ):
            _require(item, key, context)
        if item["identity_status"] == "unverified_from_git" and item["deployed_license"] != "unknown":
            raise ValueError(
                f"{context}: an unverified artifact cannot claim a deployed license"
            )
        if str(item["expected_upstream_model_id"]) not in model_doc:
            raise ValueError(
                f"expected model missing from modelos-y-licencias.md: "
                f"{item['expected_upstream_model_id']}"
            )

    external_services = manifest.get("external_ml_services", [])
    if not isinstance(external_services, list):
        raise ValueError("manifest.external_ml_services must be a list")
    for index, item in enumerate(external_services):
        context = f"manifest.external_ml_services[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be a mapping")
        _require(item, "service_id", context)
        _require(item, "status", context)
        _require(item, "deployed_model_license", context)
        license_source = str(_require(item, "upstream_code_license_source", context))
        if not license_source.startswith("https://"):
            raise ValueError(
                f"{context}.upstream_code_license_source must be HTTPS"
            )

    print(
        "Documentation manifest OK: "
        f"{len(models)} named models, "
        f"{len(runtime_artifacts)} runtime artifacts, "
        f"{len(external_services)} external ML services"
    )


if __name__ == "__main__":
    validate()
