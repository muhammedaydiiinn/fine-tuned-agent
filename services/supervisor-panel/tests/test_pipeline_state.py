from types import SimpleNamespace

from app.pipeline_state import select_actionable_candidate


def test_pipeline_does_not_offer_retired_production_model_as_candidate():
    retired = SimpleNamespace(
        id=14,
        deployment_status="inactive",
        eval_status="passed",
        metadata_json={"lifecycle_status": "retired"},
    )
    assert select_actionable_candidate([retired]) is None


def test_pipeline_selects_newest_actionable_candidate():
    evaluated = SimpleNamespace(
        id=15,
        metadata_json={"lifecycle_status": "evaluated"},
    )
    older = SimpleNamespace(
        id=13,
        metadata_json={"lifecycle_status": "candidate"},
    )

    assert select_actionable_candidate([evaluated, older]) is evaluated
