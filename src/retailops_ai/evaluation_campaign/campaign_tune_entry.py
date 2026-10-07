"""Isolated source-tree or wheel bootstrap."""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.evaluation_campaign.campaign_tune_worker import main

    main(Path(sys.argv[1]).resolve())
