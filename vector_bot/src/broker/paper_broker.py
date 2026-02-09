from src.trade.plan_types import TradePlan
from src.trade.config_live import LiveConfig

class PaperBroker:
    def execute(self, plan: TradePlan, cfg: LiveConfig) -> None:
        print("\n=== PAPER PLAN (NO ORDERS) ===")
        print(plan)
        print("=============================\n")
