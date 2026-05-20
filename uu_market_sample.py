import json

from uu_market_radar import sample_uu_prices


def main() -> int:
    output = sample_uu_prices()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
