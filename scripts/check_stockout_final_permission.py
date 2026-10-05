"""Fail before any final source download unless the exact campaign is owner authorized."""

from pathlib import Path

from retailops_ai.stockout_campaign.assembly import guard
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference"


def main() -> int:
    freeze = CampaignFreeze.model_validate_json(
        (REFERENCE / "stockout-final-campaign-v2.json").read_bytes()
    )
    permission = CampaignPermission.model_validate_json(
        (REFERENCE / "stockout-final-permission-v2.json").read_bytes()
    )
    for source in freeze.sources:
        guard(freeze, permission, source)
    bound_recipes(
        freeze,
        ScoringRecipe.model_validate_json(
            (REFERENCE / "stockout-final-selected-recipe.json").read_bytes()
        ),
        ScoringPolicy.model_validate_json(
            (REFERENCE / "stockout-final-selected-policy-v2.json").read_bytes()
        ),
    )
    print("Exact stockout campaign and policy owner permission verified; no outcomes opened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
