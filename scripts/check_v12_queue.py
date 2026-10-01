"""Real disposable v12 lifecycle/queue/worker/restart acceptance, with cached images only."""

from check_v12_lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main(include_queue=True))
