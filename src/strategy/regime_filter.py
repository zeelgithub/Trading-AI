"""
Regime filter -- strategy layer.

Classifies a symbol's daily regime (trending / ranging / expansion / dead-zone)
from ADX and ATR slope (expansion: rising ATR + a fresh range break), then
routes the symbol to exactly ONE strategy so the strategies never fight over
the same name. EMA alignment is not part of this classification -- each
strategy's own entry conditions (config/strategies.yaml) apply their own EMA
checks after the regime filter has already picked a lane.

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.config import Config
from src.common.models import Regime


class RegimeFilter:
    def __init__(self, config: Config) -> None:
        rf = config.strategies.get("regime_filter", {})
        self.adx_min = float(rf.get("trending_adx_min", 25))
        self.adx_max = float(rf.get("ranging_adx_max", 20))
        self.atr_slope_lookback = int(rf.get("atr_slope_lookback", 5))
        self.routing = rf.get("routing", {})
        bo = config.strategies.get("strategies", {}).get("breakout", {})
        self.sr_lookback = int(bo.get("indicators", {}).get("sr_lookback", 20))

    def classify(self, features: pd.DataFrame) -> Regime:
        if features.empty:
            return Regime.NONE
        last = features.iloc[-1]
        if pd.isna(last.adx) or pd.isna(last.atr):
            return Regime.NONE

        # Expansion takes precedence: rising ATR + a fresh range break.
        if len(features) > self.atr_slope_lookback:
            atr_then = features["atr"].iloc[-(self.atr_slope_lookback + 1)]
            atr_rising = not pd.isna(atr_then) and last.atr > atr_then
            if atr_rising and len(features) > self.sr_lookback + 1:
                window = features.iloc[-(self.sr_lookback + 1):-1]
                if last.close > window["high"].max() or last.close < window["low"].min():
                    return Regime.EXPANSION

        if last.adx >= self.adx_min:
            return Regime.TRENDING
        if last.adx <= self.adx_max:
            return Regime.RANGING
        return Regime.NONE  # dead zone -- stand aside

    def active_strategy(self, features: pd.DataFrame) -> str | None:
        regime = self.classify(features)
        if regime == Regime.NONE:
            return None
        return self.routing.get(regime.value)
