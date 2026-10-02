"""Real PostgreSQL/HTTP forecast read with disposable synthetic SQL-only releases."""

from check_forecast_publication import main as publication_main


def main() -> int:
    return publication_main(read=True)


if __name__ == "__main__":
    raise SystemExit(main())
