"""build_discovery_pipeline: approved weight-advisor overrides win over the
static config default. All sources disabled -- this only exercises the
composition wiring, not any real source/provider."""

from __future__ import annotations

from dataclasses import replace

from src.common.config import load_config
from src.discovery.builder import build_discovery_pipeline
from src.discovery.weight_advisor import DiscoveryWeightState, DiscoveryWeightStateStore


def _no_sources_config():
    base = load_config()
    discovery = {**base.settings.get("discovery", {}),
                "sources": {"congress": False, "technical": False, "news": False, "fundamentals": False}}
    return replace(base, settings={**base.settings, "discovery": discovery})


def test_scorer_uses_config_weights_when_no_override(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.discovery.builder.DiscoveryWeightStateStore",
        lambda: DiscoveryWeightStateStore(tmp_path / "w.json"),
    )
    pipeline = build_discovery_pipeline(_no_sources_config())
    assert pipeline.scorer.weights["congress"] == 0.60   # config/settings.yaml's discovery.weights.congress


def test_scorer_uses_approved_override_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.discovery.builder.DiscoveryWeightStateStore",
        lambda: DiscoveryWeightStateStore(tmp_path / "w.json"),
    )
    DiscoveryWeightStateStore(tmp_path / "w.json").save(
        DiscoveryWeightState(weights={"congress": 0.6, "technical": 0.2})
    )
    pipeline = build_discovery_pipeline(_no_sources_config())
    assert pipeline.scorer.weights["congress"] == 0.6
    assert pipeline.scorer.weights["technical"] == 0.2
    assert pipeline.scorer.weights["news"] == 0.15        # untouched key still from config
