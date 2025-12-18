from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .models import Action, Side, Snapshot


@dataclass(frozen=True)
class StrategyDecision:
    action: Action
    side: Optional[Side]
    score: float
    reason: str


class FundingPremiumStrategy:
    def __init__(
        self,
        prem_entry: float,
        fund_entry: float,
        prem_exit: float,
        fund_exit: float,
    ) -> None:
        self.prem_entry = prem_entry
        self.fund_entry = fund_entry
        self.prem_exit = prem_exit
        self.fund_exit = fund_exit

    @staticmethod
    def _abs(x: float) -> float:
        return x if x >= 0 else -x

    def score_sides(self, s: Snapshot) -> Tuple[float, float]:
        """
        Returns (short_carry_score, long_carry_score).
        short carry valid when premium>0 and funding>0
        long carry valid when premium<0 and funding<0
        score uses raw sum (premium + funding) for the valid side, else 0.
        """
        if s.premium is None or s.fundingRate is None:
            return 0.0, 0.0

        prem = s.premium
        fund = s.fundingRate

        short_score = (prem + fund) if (prem > 0 and fund > 0) else 0.0
        long_score = (-prem + -fund) if (prem < 0 and fund < 0) else 0.0
        return short_score, long_score

    def decide_open(self, s: Snapshot) -> StrategyDecision:
        if s.premium is None or s.fundingRate is None:
            return StrategyDecision("HOLD", None, 0.0, "missing_data")

        prem = s.premium
        fund = s.fundingRate

        # Gate: must be same sign for a carry
        if prem > 0 and fund > 0:
            # entry thresholds on absolute values
            if prem >= self.prem_entry and fund >= self.fund_entry:
                score = prem + fund
                return StrategyDecision("OPEN", "SHORT_PERP_LONG_SPOT", score, "valid_short_carry_entry")
            return StrategyDecision("HOLD", None, prem + fund, "short_carry_but_below_entry_thresholds")

        if prem < 0 and fund < 0:
            if (-prem) >= self.prem_entry and (-fund) >= self.fund_entry:
                score = (-prem) + (-fund)
                return StrategyDecision("OPEN", "LONG_PERP_SHORT_SPOT", score, "valid_long_carry_entry")
            return StrategyDecision("HOLD", None, (-prem) + (-fund), "long_carry_but_below_entry_thresholds")

        return StrategyDecision("HOLD", None, 0.0, "sign_mismatch_not_a_carry")

    def should_close(self, s: Snapshot, current_side: Side) -> StrategyDecision:
        """Close if signal decays below exit thresholds OR sign mismatch now."""
        if s.premium is None or s.fundingRate is None:
            return StrategyDecision("HOLD", current_side, 0.0, "missing_data_hold")

        prem = s.premium
        fund = s.fundingRate

        if current_side == "SHORT_PERP_LONG_SPOT":
            # if not valid carry anymore => close
            if not (prem > 0 and fund > 0):
                return StrategyDecision("CLOSE", current_side, 0.0, "short_carry_invalidated_sign_mismatch")
            if prem <= self.prem_exit or fund <= self.fund_exit:
                return StrategyDecision("CLOSE", current_side, prem + fund, "short_carry_decayed_below_exit")
            return StrategyDecision("HOLD", current_side, prem + fund, "short_carry_ok_hold")

        # LONG_PERP_SHORT_SPOT
        if not (prem < 0 and fund < 0):
            return StrategyDecision("CLOSE", current_side, 0.0, "long_carry_invalidated_sign_mismatch")
        if (-prem) <= self.prem_exit or (-fund) <= self.fund_exit:
            return StrategyDecision("CLOSE", current_side, (-prem) + (-fund), "long_carry_decayed_below_exit")
        return StrategyDecision("HOLD", current_side, (-prem) + (-fund), "long_carry_ok_hold")

    def decide(self, s: Snapshot, current_side: Optional[Side]) -> StrategyDecision:
        """Unified entry point used by main/executor."""
        if current_side is None:
            return self.decide_open(s)
        return self.should_close(s, current_side)

    def entry_gaps(self, s: Snapshot) -> Tuple[float, float]:
        """Returns (prem_gap, fund_gap) to reach entry thresholds. Never negative."""
        prem = self._abs(s.premium) if s.premium is not None else 0.0
        fund = self._abs(s.fundingRate) if s.fundingRate is not None else 0.0
        return max(0.0, self.prem_entry - prem), max(0.0, self.fund_entry - fund)
