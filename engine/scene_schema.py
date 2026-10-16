"""Versioned JSON Schema validation for scene plans."""

from __future__ import annotations

import jsonschema

from .config import load_schema

SCHEMA_VERSION = "1.0"


class PlanValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("Scene plan failed validation:\n  " + "\n  ".join(errors))
        self.errors = errors


def plan_errors(plan: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema("scene_plan.schema.json"))
    errors = [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in validator.iter_errors(plan)]
    # Structural checks the schema cannot express.
    for scene in plan.get("scenes", []):
        sid = scene.get("scene_id", "?")
        if scene.get("end_seconds", 0) < scene.get("start_seconds", 0):
            errors.append(f"{sid}: end_seconds before start_seconds")
        ids = {a.get("id") for a in scene.get("visual_actions", [])}
        orders = [a.get("draw_order") for a in scene.get("visual_actions", [])]
        if len(set(orders)) != len(orders):
            errors.append(f"{sid}: duplicate draw_order values")
        for a in scene.get("visual_actions", []):
            for ref in ("source", "target"):
                if ref in a and a[ref] not in ids:
                    errors.append(f"{sid}/{a.get('id')}: {ref} {a[ref]!r} does not exist")
            pos = a.get("position", "")
            if pos.startswith("in_hand:") and pos.split(":", 1)[1] not in ids:
                errors.append(f"{sid}/{a.get('id')}: in_hand target missing")
    return errors


def validate_plan(plan: dict) -> None:
    errors = plan_errors(plan)
    if errors:
        raise PlanValidationError(errors)
