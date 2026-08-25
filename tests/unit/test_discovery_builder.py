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


def test_registry_keys_match_canonical_sources_tuple():
    """registry.py asserts this itself at import time (an ImportError means
    the module failed to import); this just makes the invariant visible as
    an ordinary test result too."""
    from src.discovery.candidate import SOURCES
    from src.discovery.sources.registry import REGISTRY
    assert set(REGISTRY) == set(SOURCES)


def test_builder_needs_no_edits_to_pick_up_a_newly_registered_source(tmp_path, monkeypatch):
    """Proves the registry actually replaces the old if/elif chain: register
    a fake source under a name builder.py has never heard of, enable it via
    config, and confirm build_discovery_pipeline() wires it in without any
    builder.py change."""
    monkeypatch.setattr(
        "src.discovery.builder.DiscoveryWeightStateStore",
        lambda: DiscoveryWeightStateStore(tmp_path / "w.json"),
    )
    from dataclasses import dataclass, field

    from src.discovery.candidate import SignalContribution
    from src.discovery.sources.registry import REGISTRY, register

    @dataclass
    class _FakeSource:
        name: str = "fake_test_source"
        built_with_universe: list = field(default_factory=list)

        def gather(self) -> list[SignalContribution]:
            return []

    built = {}

    @register("fake_test_source")
    def _build_fake(ctx):
        src = _FakeSource(built_with_universe=ctx.universe)
        built["instance"] = src
        return src

    try:
        base = load_config()
        discovery = {**base.settings.get("discovery", {}),
                    "sources": {"fake_test_source": True}}
        config = replace(base, settings={**base.settings, "discovery": discovery})
        pipeline = build_discovery_pipeline(config)
        assert len(pipeline.sources) == 1
        assert pipeline.sources[0] is built["instance"]
    finally:
        del REGISTRY["fake_test_source"]
