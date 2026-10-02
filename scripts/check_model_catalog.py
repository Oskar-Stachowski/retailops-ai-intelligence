"""Real PostgreSQL/HTTP scoped catalog with disposable SQL-only releases."""

from check_forecast_publication import main as publication_main


def main() -> int:
    return publication_main(catalog=True)


if __name__ == "__main__":
    raise SystemExit(main())
