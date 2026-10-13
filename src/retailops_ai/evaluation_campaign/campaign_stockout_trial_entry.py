"""Bootstrap the same isolated stockout trial from source or an installed wheel."""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.evaluation_campaign.campaign_stockout_trial_worker import main

    main(Path(sys.argv[2]).resolve(), sys.argv[1])
