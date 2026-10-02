"""Real disposable v12 publication/read/restart acceptance, using cached images only."""

from check_v12_lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main(include_outputs=True))
