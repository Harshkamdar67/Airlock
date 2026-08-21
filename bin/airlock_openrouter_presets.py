#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Curated, opt-in OpenRouter preset metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import airlock_policy as policy

MAX_GUIDANCE_LENGTH = 240


@dataclass(frozen=True)
class PresetSpec:
    name: str
    display_name: str
    route: str
    model: str
    canonical_slug: str
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    evidence_date: str
    suggested_use: str
    tradeoffs: str


PRESETS = (
    PresetSpec(
        name="kimi-k3",
        display_name="Kimi K3",
        route="kimi-k3",
        model="moonshotai/kimi-k3",
        canonical_slug="moonshotai/kimi-k3-20260715",
        endpoint_provider="digitalocean",
        provider_name="DigitalOcean",
        provider_slug="digitalocean",
        quantization="unknown",
        evidence_date="2026-08-10",
        suggested_use=(
            "frontend and visual implementation, plus substantial coding agents"
        ),
        tradeoffs="slower and token-heavy",
    ),
    PresetSpec(
        name="deepseek-v4-flash-0731",
        display_name="DeepSeek V4 Flash 0731",
        route="deepseek-v4-flash-0731",
        model="deepseek/deepseek-v4-flash-0731",
        canonical_slug="deepseek/deepseek-v4-flash-20260731",
        endpoint_provider="deepinfra/fp4",
        provider_name="DeepInfra",
        provider_slug="deepinfra",
        quantization="fp4",
        evidence_date="2026-08-10",
        suggested_use=(
            "cost-sensitive coding, debugging, and bounded repository automation"
        ),
        tradeoffs=(
            "harness-sensitive tool use and weaker non-coding reliability"
        ),
    ),
    PresetSpec(
        name="qwen-3-6-27b",
        display_name="Qwen 3.6 27B",
        route="qwen-3-6-27b",
        model="qwen/qwen3.6-27b",
        canonical_slug="qwen/qwen3.6-27b-20260422",
        endpoint_provider="chutes/fp8",
        provider_name="Chutes",
        provider_slug="chutes",
        quantization="fp8",
        evidence_date="2026-08-10",
        suggested_use=(
            "bounded coding, refactoring, planning, tests, and data work"
        ),
        tradeoffs="tool loops and long-session degradation",
    ),
    PresetSpec(
        name="ox-alpha",
        display_name="Ox Alpha",
        route="ox-alpha",
        model="stealth/ox-alpha",
        canonical_slug="stealth/ox-alpha",
        endpoint_provider="stealth",
        provider_name="Stealth",
        provider_slug="stealth",
        quantization="unknown",
        evidence_date="2026-08-22",
        suggested_use=(
            "long-horizon coding agents, multi-step tool loops, and "
            "repository-scale reasoning over a 1M-token context, with "
            "screenshots and logs alongside code"
        ),
        tradeoffs=(
            "anonymous preview provider that retains prompts and completions, "
            "free pricing and availability that can end without notice, "
            "reported tool-call errors near 4.5 percent, and single-run "
            "benchmark claims"
        ),
    ),
)


def _validate_guidance(value: str, location: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_GUIDANCE_LENGTH
        or not value.isascii()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise RuntimeError(f"{location} is invalid")
    return value


def _validate_presets() -> None:
    names: set[str] = set()
    routes: set[str] = set()
    models: set[str] = set()
    for index, preset in enumerate(PRESETS):
        location = f"OpenRouter preset {index}"
        name = policy._validate_route(preset.name, f"{location} name")
        route = policy._validate_route(preset.route, f"{location} route")
        model = policy._validate_openrouter_model(
            preset.model, f"{location} model"
        )
        policy._validate_openrouter_slug(
            preset.canonical_slug, f"{location} canonical slug"
        )
        policy._validate_endpoint_provider(
            preset.endpoint_provider, f"{location} endpoint provider"
        )
        policy._validate_provider_name(
            preset.provider_name, f"{location} provider name"
        )
        policy._validate_provider_slug(
            preset.provider_slug, f"{location} provider slug"
        )
        policy._validate_quantization(
            preset.quantization, f"{location} quantization"
        )
        try:
            date.fromisoformat(preset.evidence_date)
        except ValueError as exc:
            raise RuntimeError(f"{location} evidence date is invalid") from exc
        _validate_guidance(preset.display_name, f"{location} display name")
        _validate_guidance(preset.suggested_use, f"{location} suggested use")
        _validate_guidance(preset.tradeoffs, f"{location} tradeoffs")
        if name in names or route in routes or model in models:
            raise RuntimeError("OpenRouter preset names, routes, and models must be unique")
        names.add(name)
        routes.add(route)
        models.add(model)


_validate_presets()

PRESETS_BY_NAME = {preset.name: preset for preset in PRESETS}
PRESETS_BY_MODEL = {preset.model: preset for preset in PRESETS}


def preset_by_name(name: str) -> PresetSpec | None:
    return PRESETS_BY_NAME.get(name)


def preset_by_model(model: str) -> PresetSpec | None:
    return PRESETS_BY_MODEL.get(model)
