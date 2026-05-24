import json

from uu_market_radar import sample_uu_prices


def main() -> int:
    output = sample_uu_prices()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not output["errors"]:
        return 0

    # A 403/429 circuit-breaker after partial success is a normal protective
    # stop, not a broken systemd unit. Keep hard failures visible when nothing
    # was sampled.
    return 0 if output.get("sampled_count", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
