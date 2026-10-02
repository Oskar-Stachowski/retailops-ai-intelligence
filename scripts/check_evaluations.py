"""Disposable installed HTTP/PostgreSQL development-evaluation acceptance."""

from check_forecast_publication import main as publication_main


def main() -> int:
    return publication_main(evaluations=True)


if __name__ == "__main__":
    raise SystemExit(main())
