from src.trade.config_live import LiveConfig
from src.broker.hl_broker import HyperliquidBroker

def main():
    cfg = LiveConfig()

    # Hard safety gates
    if cfg.SAFE_MODE or not cfg.ENABLE_LIVE:
        raise RuntimeError(
            "Refusing to trade: set SAFE_MODE=False and ENABLE_LIVE=True "
            "temporarily in config_live.py"
        )

    print("TEST_LIVE_TRADE: starting")

    broker = HyperliquidBroker()

    # Very small notional, just to verify end-to-end execution
    result = broker.execute_test_trade(
        cfg=cfg,
        side="BUY",
        notional_usd=10.0
    )

    print("TEST_LIVE_TRADE: result")
    print(result)

if __name__ == "__main__":
    main()
