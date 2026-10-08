"""Isolated evaluation phase bootstrap for source trees or installed wheels."""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.evaluation_campaign.campaign_evaluation_worker import main

    main(sys.argv[1], Path(sys.argv[2]).resolve())
