#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Strict, reusable policy validation for user-owned Airlock JSON.

This module uses only the Python standard library. It does not repair, merge, or
silently ignore policy input. Callers get immutable validated values or a clear
exception.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

MAX_POLICY_BYTES = 128 * 1024
MAX_OPENROUTER_ENTRIES = 10
MAX_OPENMODEL_ENDPOINTS = 16
MAX_OPENMODEL_MODELS = 32
MAX_OPENMODEL_IDENTITY_CHARS = 1024
MAX_OPENMODEL_IDENTITY_BYTES = 4096
MAX_SNAPSHOT_AGENTS = 64
# Each canonical Agent model may also have one deterministic [1m] wire alias,
# and the root may be separate from the Agent catalog.
MAX_SNAPSHOT_ROUTE_ENTRIES = 2 * (MAX_SNAPSHOT_AGENTS + 1)
MAX_SNAPSHOT_FAILOVER_ENTRIES = 64
MAX_SNAPSHOT_FAILOVER_PEERS = 8
MIN_CONTEXT_WINDOW_TOKENS = 1000
MAX_CONTEXT_WINDOW_TOKENS = 10_000_000
VALID_OVERFLOW_SHRINK_MODES = frozenset({"auto", "truncate", "summarize", "off"})
MAX_SUPPORTED_PARAMETERS = 64
CHECKED_AT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
CHECKED_AT_FUTURE_TOLERANCE_SECONDS = 24 * 60 * 60
REGISTRY_LOCK_TIMEOUT_SECONDS = 10
REGISTRY_LOCK_RETRY_SECONDS = 0.05

_ROUTE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_AGENT_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
# OpenRouter endpoint IDs are either one lower-case slug or two such slugs
# separated by one slash, for example "azure" or "deepinfra/turbo".
_ENDPOINT_PROVIDER_RE = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*)?\Z",
    re.ASCII,
)
# OpenRouter's provider-routing API uses the catalog's display-style provider
# name rather than its endpoint tag. Keep accepted names token-like and bounded;
# this is routing metadata, not user-authored prose.
_PROVIDER_NAME_RE = re.compile(
    r"[A-Za-z0-9]+(?:[ ._&'()+/-][A-Za-z0-9]+)*\Z", re.ASCII
)
_PROVIDER_SLUG_RE = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z", re.ASCII
)
_QUANTIZATION_RE = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z", re.ASCII
)
_PARAMETER_RE = re.compile(r"[a-z][a-z0-9_]*\Z", re.ASCII)
# OpenRouter routable IDs and canonical provenance slugs have exactly two
# non-empty slash-separated segments. Each segment starts and ends in lower-case
# ASCII alphanumeric text. Internal '.', '_', ':', and '-' separators are
# accepted only between alphanumeric runs. This deliberately rejects whitespace,
# controls, brackets, repeated separators, upper case, and query-like text.
_OPENROUTER_SEGMENT_RE = re.compile(
    r"[a-z0-9]+(?:[._:-][a-z0-9]+)*\Z", re.ASCII
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)
# Session snapshots also carry native exact IDs such as a Claude ID with [1m].
# Keep this generic but token-like: no whitespace, controls, shell metacharacters,
# or free-form text. Provider-specific tables belong in their integrating code.
_EXACT_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9._:/\-\[\]]{0,159}\Z", re.ASCII)
_PROFILE_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z", re.ASCII)

_REGISTRY_FIELDS = frozenset({"schema_version", "models"})
_ENTRY_FIELDS = frozenset({
    "route",
    "model",
    "endpoint_provider",
    "provider_name",
    "provider_slug",
    "quantization",
    "canonical_slug",
    "alias_target",
    "supported_parameters",
    "expiration_date",
    "checked_at",
    "enabled",
})
_OPENMODEL_REGISTRY_FIELDS = frozenset({"schema_version", "endpoints", "models"})
_OPENMODEL_ENDPOINT_FIELDS = frozenset({
    "id",
    "base_url",
    "trust",
    "protocol",
    "auth",
    "max_concurrency",
    "enabled",
})
_OPENMODEL_MODEL_FIELDS = frozenset({
    "route",
    "endpoint",
    "upstream_model",
    "accepted_response_models",
    "context_window",
    "max_output_tokens",
    "streaming",
    "tools",
    "tool_choice",
    "worker",
    "enabled",
})
_SNAPSHOT_FIELDS = frozenset({
    "schema_version",
    "protocol_version",
    "profile",
    "root_model",
    "root_provider",
    "routes",
    "agents",
    "openrouter",
    "openmodel",
    "failover",
    "context_windows",
    "route_categories",
    "effort_ceilings",
    "compactors",
    "overflow_shrink",
})
_AGENT_FIELDS = frozenset({"model", "provider", "extra_usage"})
_ROUTE_CATEGORIES = frozenset({"included", "extra", "metered", "unknown"})
_OPENROUTER_SNAPSHOT_REQUIRED_FIELDS = frozenset({
    "endpoint_provider",
    "provider_name",
    "provider_slug",
    "quantization",
    "canonical_slug",
})
_OPENROUTER_SNAPSHOT_FIELDS = (
    _OPENROUTER_SNAPSHOT_REQUIRED_FIELDS | {"effort_ceiling"}
)
_OPENMODEL_SNAPSHOT_FIELDS = frozenset({"endpoints", "models"})
_OPENMODEL_ENDPOINT_SNAPSHOT_FIELDS = frozenset({
    "base_url",
    "trust",
    "protocol",
    "auth",
    "max_concurrency",
})
_OPENMODEL_MODEL_SNAPSHOT_FIELDS = frozenset({
    "endpoint",
    "upstream_model",
    "accepted_response_models",
    "context_window",
    "max_output_tokens",
    "streaming",
    "tools",
    "tool_choice",
})
_OPENMODEL_TRUST = "loopback"
_OPENMODEL_PROTOCOL = "openai-chat-completions-v1"
_OPENMODEL_AUTH = "none"
_OPENMODEL_TOOL_SUPPORT = frozenset({"none", "single", "parallel"})
_OPENMODEL_TOOL_CHOICES = frozenset({"auto", "named", "none", "required"})
_EFFORT_CEILINGS = frozenset({"low", "medium", "high", "xhigh", "max"})
_PROVIDERS = frozenset({"anthropic", "openai", "grok", "openrouter", "openmodel"})


class PolicyValidationError(ValueError):
    """Raised when policy bytes, structure, or values are invalid."""


class PolicyFileError(PolicyValidationError):
    """Raised when a policy path is unsafe or cannot be read safely."""


class PolicyDurabilityError(PolicyFileError):
    """Raised after replacement when directory durability cannot be confirmed."""


class RegistryNotFoundError(FileNotFoundError):
    """Raised when the optional user-owned registry does not exist."""


class RegistryConflictError(PolicyFileError):
    """Raised when a registry changed after a management command read it."""


class RegistryBusyError(PolicyFileError):
    """Raised when another registry management command still holds the lock."""


@dataclass(frozen=True)
class OpenRouterEntry:
    route: str
    model: str
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    canonical_slug: str
    alias_target: None
    supported_parameters: tuple[str, ...]
    expiration_date: str | None
    checked_at: int
    enabled: bool

    @property
    def agent_name(self) -> str:
        return f"airlock-or-{self.route}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "model": self.model,
            "endpoint_provider": self.endpoint_provider,
            "provider_name": self.provider_name,
            "provider_slug": self.provider_slug,
            "quantization": self.quantization,
            "canonical_slug": self.canonical_slug,
            "alias_target": None,
            "supported_parameters": list(self.supported_parameters),
            "expiration_date": self.expiration_date,
            "checked_at": self.checked_at,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class OpenRouterRegistry:
    schema_version: int
    models: tuple[OpenRouterEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "models": [entry.to_dict() for entry in self.models],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())


@dataclass(frozen=True)
class OpenModelEndpoint:
    id: str
    base_url: str
    trust: str
    protocol: str
    auth: str
    max_concurrency: int
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "base_url": self.base_url,
            "trust": self.trust,
            "protocol": self.protocol,
            "auth": self.auth,
            "max_concurrency": self.max_concurrency,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class OpenModelEntry:
    route: str
    endpoint: str
    upstream_model: str
    accepted_response_models: tuple[str, ...]
    context_window: int
    max_output_tokens: int
    streaming: bool
    tools: str
    tool_choice: tuple[str, ...]
    worker: bool
    enabled: bool

    @property
    def wire_model(self) -> str:
        return f"openmodel/{self.route}"

    @property
    def agent_name(self) -> str:
        return f"airlock-om-{self.route}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "endpoint": self.endpoint,
            "upstream_model": self.upstream_model,
            "accepted_response_models": list(self.accepted_response_models),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "streaming": self.streaming,
            "tools": self.tools,
            "tool_choice": list(self.tool_choice),
            "worker": self.worker,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class OpenModelRegistry:
    schema_version: int
    endpoints: tuple[OpenModelEndpoint, ...]
    models: tuple[OpenModelEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
            "models": [entry.to_dict() for entry in self.models],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())

    def endpoint(self, endpoint_id: str) -> OpenModelEndpoint | None:
        return next(
            (endpoint for endpoint in self.endpoints if endpoint.id == endpoint_id),
            None,
        )

    def active_models(self) -> tuple[OpenModelEntry, ...]:
        enabled_endpoints = {
            endpoint.id for endpoint in self.endpoints if endpoint.enabled
        }
        return tuple(
            entry
            for entry in self.models
            if entry.enabled and entry.endpoint in enabled_endpoints
        )


@dataclass(frozen=True)
class SnapshotAgent:
    model: str
    provider: str
    extra_usage: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "model": self.model,
            "provider": self.provider,
            "extra_usage": self.extra_usage,
        }


@dataclass(frozen=True)
class SnapshotOpenRouterRoute:
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    canonical_slug: str
    effort_ceiling: str = "high"
    effort_ceiling_explicit: bool = True

    def to_dict(self) -> dict[str, str]:
        result = {
            "endpoint_provider": self.endpoint_provider,
            "provider_name": self.provider_name,
            "provider_slug": self.provider_slug,
            "quantization": self.quantization,
            "canonical_slug": self.canonical_slug,
        }
        if self.effort_ceiling_explicit:
            result["effort_ceiling"] = self.effort_ceiling
        return result


@dataclass(frozen=True)
class SnapshotOpenModelEndpoint:
    base_url: str
    trust: str
    protocol: str
    auth: str
    max_concurrency: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "trust": self.trust,
            "protocol": self.protocol,
            "auth": self.auth,
            "max_concurrency": self.max_concurrency,
        }


@dataclass(frozen=True)
class SnapshotOpenModelRoute:
    endpoint: str
    upstream_model: str
    accepted_response_models: tuple[str, ...]
    context_window: int
    max_output_tokens: int
    streaming: bool
    tools: str
    tool_choice: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "upstream_model": self.upstream_model,
            "accepted_response_models": list(self.accepted_response_models),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "streaming": self.streaming,
            "tools": self.tools,
            "tool_choice": list(self.tool_choice),
        }


@dataclass(frozen=True)
class SnapshotOpenModel:
    endpoints: Mapping[str, SnapshotOpenModelEndpoint]
    models: Mapping[str, SnapshotOpenModelRoute]

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoints": {
                endpoint_id: endpoint.to_dict()
                for endpoint_id, endpoint in self.endpoints.items()
            },
            "models": {
                model: metadata.to_dict()
                for model, metadata in self.models.items()
            },
        }


@dataclass(frozen=True)
class SessionSnapshot:
    schema_version: int
    protocol_version: int
    profile: str
    root_model: str
    root_provider: str
    routes: Mapping[str, str]
    agents: Mapping[str, SnapshotAgent]
    openrouter: Mapping[str, SnapshotOpenRouterRoute]
    openmodel: SnapshotOpenModel = field(
        default_factory=lambda: SnapshotOpenModel(
            endpoints=MappingProxyType({}),
            models=MappingProxyType({}),
        )
    )
    # Ordered same-category replacement models per routed model ID. Snapshots
    # written before rate-limit failover existed carry an empty map.
    failover: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Known context-window sizes (tokens) per routed model ID. Snapshots
    # written before overflow-aware handoff existed carry an empty map; a
    # model absent from this map is simply never pre-screened for size.
    context_windows: Mapping[str, int] = field(default_factory=dict)
    # Console-facing route facts are frozen from the same validated worker
    # metadata that built this session's handoff groups. Older snapshots omit
    # them and the router reports an honest unknown instead.
    route_categories: Mapping[str, str] = field(default_factory=dict)
    effort_ceilings: Mapping[str, str] = field(default_factory=dict)
    # Per-provider compaction worker used when no failover peer can fit an
    # overflowing conversation. Snapshots written before overflow-aware
    # handoff existed carry an empty map, which disables compaction.
    compactors: Mapping[str, str] = field(default_factory=dict)
    # What the router does when every candidate window is too small:
    # "auto" (compact through the provider compactor, else truncate),
    # "truncate", "summarize", or "off".
    overflow_shrink: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "profile": self.profile,
            "root_model": self.root_model,
            "root_provider": self.root_provider,
            "routes": dict(self.routes),
            "agents": {
                name: agent.to_dict() for name, agent in self.agents.items()
            },
            "openrouter": {
                model: metadata.to_dict()
                for model, metadata in self.openrouter.items()
            },
            "openmodel": self.openmodel.to_dict(),
            "failover": {
                model: list(peers) for model, peers in self.failover.items()
            },
            "context_windows": dict(self.context_windows),
            "route_categories": dict(self.route_categories),
            "effort_ceilings": dict(self.effort_ceilings),
            "compactors": dict(self.compactors),
            "overflow_shrink": self.overflow_shrink,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())


def canonical_json_bytes(value: Any) -> bytes:
    """Return compact, key-sorted, ASCII JSON bytes for a JSON-compatible value."""

    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PolicyValidationError(f"value is not canonical JSON: {exc}") from exc
    return text.encode("ascii")


def sha256_bytes(value: bytes) -> str:
    """Return the lower-case hexadecimal SHA-256 digest of bytes."""

    if not isinstance(value, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of canonical JSON bytes."""

    return sha256_bytes(canonical_json_bytes(value))


def _reject_json_constant(value: str) -> Any:
    raise PolicyValidationError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyValidationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def load_json_bytes(raw: bytes, *, max_bytes: int = MAX_POLICY_BYTES) -> Any:
    """Decode bounded UTF-8 JSON while rejecting BOMs and duplicate keys."""

    if not isinstance(raw, bytes):
        raise TypeError("load_json_bytes requires bytes")
    if len(raw) > max_bytes:
        raise PolicyValidationError(
            f"JSON input exceeds the {max_bytes}-byte limit"
        )
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PolicyValidationError("UTF-8 BOM is not allowed")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PolicyValidationError("JSON input is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except PolicyValidationError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PolicyValidationError(f"malformed JSON: {exc}") from exc


def _require_exact_fields(
    value: Any, expected: frozenset[str], location: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PolicyValidationError(f"{location} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected, key=lambda item: repr(item))
        details = []
        if missing:
            details.append(f"missing {missing!r}")
        if unknown:
            details.append(f"unknown {unknown!r}")
        raise PolicyValidationError(f"{location} has invalid fields: {', '.join(details)}")
    return value


def _require_integer(value: Any, location: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise PolicyValidationError(f"{location} must be an integer")
    if minimum is not None and value < minimum:
        raise PolicyValidationError(f"{location} must be at least {minimum}")
    return value


def _require_string(value: Any, location: str) -> str:
    if type(value) is not str:
        raise PolicyValidationError(f"{location} must be a string")
    return value


def _now_timestamp(now: int | float | datetime | None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise PolicyValidationError("now datetime must include a timezone")
        timestamp = now.timestamp()
    elif type(now) in {int, float}:
        timestamp = float(now)
    else:
        raise PolicyValidationError("now must be Unix seconds or a timezone-aware datetime")
    if not math.isfinite(timestamp) or timestamp < 0:
        raise PolicyValidationError("now must be a finite non-negative time")
    return timestamp


def _validate_route(value: Any, location: str) -> str:
    route = _require_string(value, location)
    if not (1 <= len(route) <= 40) or _ROUTE_RE.fullmatch(route) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-40 character lower-case route slug"
        )
    agent_name = f"airlock-or-{route}"
    if len(agent_name) > 64 or _AGENT_NAME_RE.fullmatch(agent_name) is None:
        raise PolicyValidationError(f"{location} does not form a valid Agent name")
    return route


def _validate_openrouter_slug(value: Any, location: str) -> str:
    slug = _require_string(value, location)
    if not (3 <= len(slug) <= 160) or not slug.isascii():
        raise PolicyValidationError(
            f"{location} must be a 3-160 character ASCII OpenRouter slug"
        )
    if slug.count("/") != 1:
        raise PolicyValidationError(f"{location} must contain exactly one slash")
    owner, name = slug.split("/", 1)
    if (
        _OPENROUTER_SEGMENT_RE.fullmatch(owner) is None
        or _OPENROUTER_SEGMENT_RE.fullmatch(name) is None
    ):
        raise PolicyValidationError(f"{location} is not a valid OpenRouter slug")
    return slug


def _validate_openrouter_model(value: Any, location: str) -> str:
    model = _validate_openrouter_slug(value, location)
    owner, name = model.split("/", 1)
    if model.endswith(":free") or model.endswith(":extended"):
        raise PolicyValidationError(f"{location} must not select a model variant")
    # OpenRouter's own namespace and auto/latest tokens identify router-owned or
    # moving targets rather than a stable vendor model. Reject them conservatively.
    name_tokens = re.split(r"[._:-]", name)
    if owner in {"openrouter", "router"} or any(
        token in {"auto", "latest"} for token in name_tokens
    ):
        raise PolicyValidationError(f"{location} must identify a fixed model")
    return model


def _validate_endpoint_provider(value: Any, location: str) -> str:
    provider = _require_string(value, location)
    if not (1 <= len(provider) <= 80) or _ENDPOINT_PROVIDER_RE.fullmatch(provider) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-80 character lower-case endpoint slug"
        )
    return provider


def _validate_provider_name(value: Any, location: str) -> str:
    provider = _require_string(value, location)
    if not (1 <= len(provider) <= 80) or _PROVIDER_NAME_RE.fullmatch(provider) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-80 character OpenRouter provider name"
        )
    return provider


def _validate_provider_slug(value: Any, location: str) -> str:
    provider = _require_string(value, location)
    if not (1 <= len(provider) <= 80) or _PROVIDER_SLUG_RE.fullmatch(provider) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-80 character lower-case provider slug"
        )
    return provider


def _validate_quantization(value: Any, location: str) -> str:
    quantization = _require_string(value, location)
    if (
        not (1 <= len(quantization) <= 32)
        or _QUANTIZATION_RE.fullmatch(quantization) is None
    ):
        raise PolicyValidationError(
            f"{location} must be a 1-32 character lower-case quantization slug"
        )
    return quantization


def _validate_parameters(value: Any, location: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise PolicyValidationError(f"{location} must be an array")
    if len(value) > MAX_SUPPORTED_PARAMETERS:
        raise PolicyValidationError(
            f"{location} must contain at most {MAX_SUPPORTED_PARAMETERS} items"
        )
    parameters: list[str] = []
    for index, item in enumerate(value):
        parameter = _require_string(item, f"{location}[{index}]")
        if not (1 <= len(parameter) <= 64) or _PARAMETER_RE.fullmatch(parameter) is None:
            raise PolicyValidationError(
                f"{location}[{index}] must be a 1-64 character lower-case parameter name"
            )
        parameters.append(parameter)
    if parameters != sorted(parameters):
        raise PolicyValidationError(f"{location} must be sorted")
    if len(parameters) != len(set(parameters)):
        raise PolicyValidationError(f"{location} must not contain duplicates")
    missing = {"tools", "tool_choice"} - set(parameters)
    if missing:
        raise PolicyValidationError(
            f"{location} is missing required parameters: {sorted(missing)!r}"
        )
    return tuple(parameters)


def _validate_expiration(
    value: Any, location: str, today: date, *, require_fresh: bool = True
) -> str | None:
    if value is None:
        return None
    expiration = _require_string(value, location)
    if _DATE_RE.fullmatch(expiration) is None:
        raise PolicyValidationError(f"{location} must use strict YYYY-MM-DD form")
    try:
        parsed = date.fromisoformat(expiration)
    except ValueError as exc:
        raise PolicyValidationError(f"{location} is not a real calendar date") from exc
    if require_fresh and parsed < today:
        raise PolicyValidationError(f"{location} is expired")
    return expiration


def _validate_checked_at(
    value: Any,
    location: str,
    now_timestamp: float,
    *,
    require_fresh: bool = True,
) -> int:
    checked_at = _require_integer(value, location, minimum=1)
    if checked_at > now_timestamp + CHECKED_AT_FUTURE_TOLERANCE_SECONDS:
        raise PolicyValidationError(f"{location} is more than 24 hours in the future")
    if require_fresh and now_timestamp - checked_at > CHECKED_AT_MAX_AGE_SECONDS:
        raise PolicyValidationError(f"{location} is older than 30 days")
    return checked_at


def validate_openrouter_registry(
    value: Any,
    *,
    now: int | float | datetime | None = None,
    require_fresh: bool = True,
) -> OpenRouterRegistry:
    """Validate a decoded OpenRouter registry and return an immutable registry."""

    now_timestamp = _now_timestamp(now)
    today = datetime.fromtimestamp(now_timestamp, timezone.utc).date()
    registry = _require_exact_fields(value, _REGISTRY_FIELDS, "registry")
    schema_version = _require_integer(registry["schema_version"], "schema_version")
    if schema_version != 1:
        raise PolicyValidationError("schema_version must be 1")
    models_value = registry["models"]
    if type(models_value) is not list:
        raise PolicyValidationError("models must be an array")
    if len(models_value) > MAX_OPENROUTER_ENTRIES:
        raise PolicyValidationError(
            f"models must contain at most {MAX_OPENROUTER_ENTRIES} entries"
        )

    entries: list[OpenRouterEntry] = []
    routes: set[str] = set()
    models: set[str] = set()
    agent_names: set[str] = set()
    for index, raw_entry in enumerate(models_value):
        location = f"models[{index}]"
        entry = _require_exact_fields(raw_entry, _ENTRY_FIELDS, location)
        route = _validate_route(entry["route"], f"{location}.route")
        model = _validate_openrouter_model(entry["model"], f"{location}.model")
        canonical_slug = _validate_openrouter_slug(
            entry["canonical_slug"], f"{location}.canonical_slug"
        )
        endpoint_provider = _validate_endpoint_provider(
            entry["endpoint_provider"], f"{location}.endpoint_provider"
        )
        provider_name = _validate_provider_name(
            entry["provider_name"], f"{location}.provider_name"
        )
        provider_slug = _validate_provider_slug(
            entry["provider_slug"], f"{location}.provider_slug"
        )
        quantization = _validate_quantization(
            entry["quantization"], f"{location}.quantization"
        )
        if entry["alias_target"] is not None:
            raise PolicyValidationError(f"{location}.alias_target must be null")
        supported_parameters = _validate_parameters(
            entry["supported_parameters"], f"{location}.supported_parameters"
        )
        expiration_date = _validate_expiration(
            entry["expiration_date"],
            f"{location}.expiration_date",
            today,
            require_fresh=require_fresh,
        )
        checked_at = _validate_checked_at(
            entry["checked_at"],
            f"{location}.checked_at",
            now_timestamp,
            require_fresh=require_fresh,
        )
        enabled = entry["enabled"]
        if type(enabled) is not bool:
            raise PolicyValidationError(f"{location}.enabled must be a boolean")
        agent_name = f"airlock-or-{route}"
        if route in routes:
            raise PolicyValidationError(f"duplicate route: {route!r}")
        if model in models:
            raise PolicyValidationError(f"duplicate model: {model!r}")
        if agent_name in agent_names:
            raise PolicyValidationError(f"duplicate Agent name: {agent_name!r}")
        routes.add(route)
        models.add(model)
        agent_names.add(agent_name)
        entries.append(OpenRouterEntry(
            route=route,
            model=model,
            endpoint_provider=endpoint_provider,
            provider_name=provider_name,
            provider_slug=provider_slug,
            quantization=quantization,
            canonical_slug=canonical_slug,
            alias_target=None,
            supported_parameters=supported_parameters,
            expiration_date=expiration_date,
            checked_at=checked_at,
            enabled=enabled,
        ))

    result = OpenRouterRegistry(schema_version=schema_version, models=tuple(entries))
    if len(result.canonical_bytes()) > MAX_POLICY_BYTES:
        raise PolicyValidationError("canonical registry exceeds the 128 KiB limit")
    return result


def _validate_openmodel_name(value: Any, location: str) -> str:
    name = _require_string(value, location)
    if not (1 <= len(name) <= 40) or _ROUTE_RE.fullmatch(name) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-40 character lower-case slug"
        )
    return name


def validate_openmodel_base_url(value: Any, location: str = "base_url") -> str:
    """Validate the one canonical endpoint form supported by the local MVP."""

    base_url = _require_string(value, location)
    match = re.fullmatch(
        r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/v1",
        base_url,
        re.ASCII,
    )
    if match is None:
        raise PolicyValidationError(
            f"{location} must use canonical http://127.0.0.1:<port>/v1 form"
        )
    port_text = match.group(1)
    port = int(port_text)
    if port > 65535 or str(port) != port_text:
        raise PolicyValidationError(f"{location} contains an invalid port")
    return base_url


def validate_openmodel_identity(value: Any, location: str = "model identity") -> str:
    """Validate an opaque private upstream identity without exposing its value."""

    identity = _require_string(value, location)
    if (
        not identity
        or identity != identity.strip()
        or len(identity) > MAX_OPENMODEL_IDENTITY_CHARS
        or any(unicodedata.category(character).startswith("C") for character in identity)
    ):
        raise PolicyValidationError(
            f"{location} must be a bounded printable model identity"
        )
    try:
        encoded = identity.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PolicyValidationError(
            f"{location} must be valid Unicode text"
        ) from exc
    if len(encoded) > MAX_OPENMODEL_IDENTITY_BYTES:
        raise PolicyValidationError(
            f"{location} exceeds the private model identity size limit"
        )
    return identity


def _validate_openmodel_identities(value: Any, location: str) -> tuple[str, ...]:
    if type(value) is not list or not (1 <= len(value) <= 16):
        raise PolicyValidationError(f"{location} must contain 1-16 model identities")
    identities = tuple(
        validate_openmodel_identity(item, f"{location}[{index}]")
        for index, item in enumerate(value)
    )
    if list(identities) != sorted(identities):
        raise PolicyValidationError(f"{location} must be sorted")
    if len(identities) != len(set(identities)):
        raise PolicyValidationError(f"{location} must not contain duplicates")
    return identities


def _validate_openmodel_tool_support(value: Any, location: str) -> str:
    support = _require_string(value, location)
    if support not in _OPENMODEL_TOOL_SUPPORT:
        raise PolicyValidationError(
            f"{location} must be none, single, or parallel"
        )
    return support


def _validate_openmodel_tool_choices(
    value: Any, location: str, *, tool_support: str
) -> tuple[str, ...]:
    if type(value) is not list or len(value) > len(_OPENMODEL_TOOL_CHOICES):
        raise PolicyValidationError(f"{location} must be an array of declared modes")
    choices: list[str] = []
    for index, item in enumerate(value):
        choice = _require_string(item, f"{location}[{index}]")
        if choice not in _OPENMODEL_TOOL_CHOICES:
            raise PolicyValidationError(
                f"{location}[{index}] must be auto, named, none, or required"
            )
        choices.append(choice)
    if choices != sorted(choices):
        raise PolicyValidationError(f"{location} must be sorted")
    if len(choices) != len(set(choices)):
        raise PolicyValidationError(f"{location} must not contain duplicates")
    if tool_support == "none" and choices:
        raise PolicyValidationError(
            f"{location} must be empty when tools is none"
        )
    if tool_support != "none" and "auto" not in choices:
        raise PolicyValidationError(
            f"{location} must declare auto when tools are enabled"
        )
    return tuple(choices)


def validate_openmodel_registry(value: Any) -> OpenModelRegistry:
    """Validate a decoded loopback open-model registry."""

    registry = _require_exact_fields(
        value, _OPENMODEL_REGISTRY_FIELDS, "open-model registry"
    )
    schema_version = _require_integer(
        registry["schema_version"], "schema_version"
    )
    if schema_version != 1:
        raise PolicyValidationError("schema_version must be 1")

    raw_endpoints = registry["endpoints"]
    if type(raw_endpoints) is not list:
        raise PolicyValidationError("endpoints must be an array")
    if len(raw_endpoints) > MAX_OPENMODEL_ENDPOINTS:
        raise PolicyValidationError(
            f"endpoints must contain at most {MAX_OPENMODEL_ENDPOINTS} entries"
        )
    endpoints: list[OpenModelEndpoint] = []
    endpoint_ids: set[str] = set()
    base_urls: set[str] = set()
    for index, raw_endpoint in enumerate(raw_endpoints):
        location = f"endpoints[{index}]"
        endpoint = _require_exact_fields(
            raw_endpoint, _OPENMODEL_ENDPOINT_FIELDS, location
        )
        endpoint_id = _validate_openmodel_name(
            endpoint["id"], f"{location}.id"
        )
        base_url = validate_openmodel_base_url(
            endpoint["base_url"], f"{location}.base_url"
        )
        trust = _require_string(endpoint["trust"], f"{location}.trust")
        protocol = _require_string(endpoint["protocol"], f"{location}.protocol")
        auth = _require_string(endpoint["auth"], f"{location}.auth")
        if trust != _OPENMODEL_TRUST:
            raise PolicyValidationError(f"{location}.trust must be loopback")
        if protocol != _OPENMODEL_PROTOCOL:
            raise PolicyValidationError(
                f"{location}.protocol must be {_OPENMODEL_PROTOCOL}"
            )
        if auth != _OPENMODEL_AUTH:
            raise PolicyValidationError(f"{location}.auth must be none")
        max_concurrency = _require_integer(
            endpoint["max_concurrency"],
            f"{location}.max_concurrency",
            minimum=1,
        )
        if max_concurrency > 64:
            raise PolicyValidationError(
                f"{location}.max_concurrency must be at most 64"
            )
        enabled = endpoint["enabled"]
        if type(enabled) is not bool:
            raise PolicyValidationError(f"{location}.enabled must be a boolean")
        if endpoint_id in endpoint_ids:
            raise PolicyValidationError(f"duplicate endpoint id: {endpoint_id!r}")
        if base_url in base_urls:
            raise PolicyValidationError("duplicate open-model endpoint URL")
        endpoint_ids.add(endpoint_id)
        base_urls.add(base_url)
        endpoints.append(OpenModelEndpoint(
            id=endpoint_id,
            base_url=base_url,
            trust=trust,
            protocol=protocol,
            auth=auth,
            max_concurrency=max_concurrency,
            enabled=enabled,
        ))

    raw_models = registry["models"]
    if type(raw_models) is not list:
        raise PolicyValidationError("models must be an array")
    if len(raw_models) > MAX_OPENMODEL_MODELS:
        raise PolicyValidationError(
            f"models must contain at most {MAX_OPENMODEL_MODELS} entries"
        )
    models: list[OpenModelEntry] = []
    routes: set[str] = set()
    wire_models: set[str] = set()
    agent_names: set[str] = set()
    endpoint_requests: set[tuple[str, str]] = set()
    endpoint_responses: set[tuple[str, str]] = set()
    for index, raw_model in enumerate(raw_models):
        location = f"models[{index}]"
        model = _require_exact_fields(
            raw_model, _OPENMODEL_MODEL_FIELDS, location
        )
        route = _validate_openmodel_name(model["route"], f"{location}.route")
        endpoint_id = _validate_openmodel_name(
            model["endpoint"], f"{location}.endpoint"
        )
        if endpoint_id not in endpoint_ids:
            raise PolicyValidationError(
                f"{location}.endpoint does not reference a declared endpoint"
            )
        upstream_model = validate_openmodel_identity(
            model["upstream_model"], f"{location}.upstream_model"
        )
        accepted = _validate_openmodel_identities(
            model["accepted_response_models"],
            f"{location}.accepted_response_models",
        )
        context_window = _require_integer(
            model["context_window"], f"{location}.context_window"
        )
        if not MIN_CONTEXT_WINDOW_TOKENS <= context_window <= MAX_CONTEXT_WINDOW_TOKENS:
            raise PolicyValidationError(
                f"{location}.context_window must be between "
                f"{MIN_CONTEXT_WINDOW_TOKENS} and {MAX_CONTEXT_WINDOW_TOKENS}"
            )
        max_output_tokens = _require_integer(
            model["max_output_tokens"],
            f"{location}.max_output_tokens",
            minimum=1,
        )
        if max_output_tokens > context_window:
            raise PolicyValidationError(
                f"{location}.max_output_tokens must not exceed context_window"
            )
        streaming = model["streaming"]
        if type(streaming) is not bool:
            raise PolicyValidationError(f"{location}.streaming must be a boolean")
        tools = _validate_openmodel_tool_support(
            model["tools"], f"{location}.tools"
        )
        tool_choice = _validate_openmodel_tool_choices(
            model["tool_choice"], f"{location}.tool_choice", tool_support=tools
        )
        worker = model["worker"]
        enabled = model["enabled"]
        if type(worker) is not bool:
            raise PolicyValidationError(f"{location}.worker must be a boolean")
        if type(enabled) is not bool:
            raise PolicyValidationError(f"{location}.enabled must be a boolean")

        wire_model = f"openmodel/{route}"
        agent_name = f"airlock-om-{route}"
        request_binding = (endpoint_id, upstream_model)
        response_bindings = {(endpoint_id, identity) for identity in accepted}
        if route in routes:
            raise PolicyValidationError(f"duplicate route: {route!r}")
        if wire_model in wire_models:
            raise PolicyValidationError(f"duplicate wire model: {wire_model!r}")
        if agent_name in agent_names:
            raise PolicyValidationError(f"duplicate Agent name: {agent_name!r}")
        if request_binding in endpoint_requests:
            raise PolicyValidationError(
                "duplicate endpoint and upstream model binding"
            )
        if response_bindings & endpoint_responses:
            raise PolicyValidationError(
                "accepted response identity is ambiguous on its endpoint"
            )
        routes.add(route)
        wire_models.add(wire_model)
        agent_names.add(agent_name)
        endpoint_requests.add(request_binding)
        endpoint_responses.update(response_bindings)
        models.append(OpenModelEntry(
            route=route,
            endpoint=endpoint_id,
            upstream_model=upstream_model,
            accepted_response_models=accepted,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            streaming=streaming,
            tools=tools,
            tool_choice=tool_choice,
            worker=worker,
            enabled=enabled,
        ))

    result = OpenModelRegistry(
        schema_version=schema_version,
        endpoints=tuple(endpoints),
        models=tuple(models),
    )
    if len(result.canonical_bytes()) > MAX_POLICY_BYTES:
        raise PolicyValidationError(
            "canonical open-model registry exceeds the 128 KiB limit"
        )
    return result


def _validate_posix_owner_and_mode(
    file_stat: os.stat_result, *, require_private_read: bool = False
) -> None:
    """Enforce user ownership and the requested POSIX sharing boundary."""

    get_effective_uid = getattr(os, "geteuid", None)
    if get_effective_uid is None:
        return
    if file_stat.st_uid != get_effective_uid():
        raise PolicyFileError("registry must be owned by the effective user")
    if file_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PolicyFileError("registry must not be writable by group or other")
    if require_private_read and file_stat.st_mode & (
        stat.S_IRWXG | stat.S_IRWXO
    ):
        raise PolicyFileError(
            "private registry must not be accessible by group or other"
        )


def _windows_current_user_sid() -> str:
    """Return the current process token's SID without shelling out."""

    if os.name != "nt":
        raise PolicyFileError("Windows ACL inspection is unavailable")
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    token_query = 0x0008
    token_user = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise PolicyFileError("cannot inspect the current Windows user")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise PolicyFileError("cannot inspect the current Windows user")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise PolicyFileError("cannot inspect the current Windows user")
        token_details = ctypes.cast(
            buffer, ctypes.POINTER(TokenUser)
        ).contents
        sid_pointer = token_details.User.Sid
        if not sid_pointer:
            raise PolicyFileError("cannot inspect the current Windows user")
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise PolicyFileError("cannot inspect the current Windows user")
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def _windows_sid_text(sid_pointer: int) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_pointer), ctypes.byref(text)
    ):
        raise PolicyFileError("cannot inspect a Windows registry ACL")
    try:
        return text.value
    finally:
        kernel32.LocalFree(text)


def _validate_windows_path_security(
    path: Path,
    *,
    require_current_owner: bool = True,
    require_private_read: bool = False,
) -> None:
    """Reject Windows paths outside the requested owner access boundary."""

    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    se_file_object = 1
    acl_size_information_class = 2
    access_allowed_ace_type = 0
    write_mask = (
        0x00000002  # FILE_WRITE_DATA
        | 0x00000004  # FILE_APPEND_DATA
        | 0x00000010  # FILE_WRITE_EA
        | 0x00000040  # FILE_DELETE_CHILD
        | 0x00000100  # FILE_WRITE_ATTRIBUTES
        | 0x00010000  # DELETE
        | 0x00040000  # WRITE_DAC
        | 0x00080000  # WRITE_OWNER
        | 0x10000000  # GENERIC_ALL
        | 0x40000000  # GENERIC_WRITE
    )
    read_data_mask = (
        0x00000001  # FILE_READ_DATA
        | 0x10000000  # GENERIC_ALL
        | 0x80000000  # GENERIC_READ
    )

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor.value or not owner.value or not dacl.value:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
        raise PolicyFileError("cannot inspect the Windows registry ACL")
    try:
        current_user = _windows_current_user_sid()
        owner_sid = _windows_sid_text(owner.value)
        if require_current_owner and owner_sid != current_user:
            raise PolicyFileError("registry path must be owned by the current Windows user")
        allowed_principals = {
            current_user,
            "S-1-5-18",  # Local System
            "S-1-5-32-544",  # Built-in Administrators
            "S-1-3-4",  # Owner Rights
        }
        details = AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(details),
            ctypes.sizeof(details),
            acl_size_information_class,
        ):
            raise PolicyFileError("cannot inspect the Windows registry ACL")
        for index in range(details.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise PolicyFileError("cannot inspect the Windows registry ACL")
            header = AceHeader.from_address(ace_pointer.value)
            mask = wintypes.DWORD.from_address(ace_pointer.value + 4).value
            if header.AceType != access_allowed_ace_type:
                if header.AceType in {4, 5, 9, 11} and mask & write_mask:
                    raise PolicyFileError(
                        "registry ACL contains an unsupported write grant"
                    )
                if (
                    require_private_read
                    and header.AceType in {4, 5, 9, 11}
                    and mask & read_data_mask
                ):
                    raise PolicyFileError(
                        "private registry ACL contains an unsupported read grant"
                    )
                continue
            sid = _windows_sid_text(ace_pointer.value + 8)
            if mask & write_mask and sid not in allowed_principals:
                raise PolicyFileError(
                    "registry path is writable by another Windows principal"
                )
            if (
                require_private_read
                and mask & read_data_mask
                and sid not in allowed_principals
            ):
                raise PolicyFileError(
                    "private registry is readable by another Windows principal"
                )
    finally:
        kernel32.LocalFree(descriptor)


def _protect_windows_path(path: Path) -> None:
    """Set a protected current-user, System, and Administrators Windows DACL."""

    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000
    sddl_revision_1 = 1
    current_user = _windows_current_user_sid()
    sddl = (
        f"O:{current_user}D:P"
        f"(A;;FA;;;{current_user})(A;;FA;;;SY)(A;;FA;;;BA)"
    )
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        sddl_revision_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        raise PolicyFileError("cannot create a private Windows registry ACL")
    try:
        if not advapi32.SetFileSecurityW(
            str(path),
            owner_security_information
            | dacl_security_information
            | protected_dacl_security_information,
            descriptor,
        ):
            raise PolicyFileError("cannot apply a private Windows registry ACL")
    finally:
        kernel32.LocalFree(descriptor)
    _validate_windows_path_security(path)


def protect_private_path(path: str | os.PathLike[str]) -> None:
    """Give a managed file the private owner and DACL this module requires.

    An elevated Windows administrator creates files owned by the built-in
    Administrators group rather than by their own account, so a file that is
    only chmod-ed still fails the owner rule this module enforces on read.
    """

    _protect_windows_path(Path(path))


def _read_regular_file(
    path: str | os.PathLike[str], *, require_private_read: bool = False
) -> bytes:
    policy_path = Path(path)
    try:
        before = os.lstat(policy_path)
    except FileNotFoundError as exc:
        raise RegistryNotFoundError(f"registry does not exist: {policy_path}") from exc
    except OSError as exc:
        raise PolicyFileError(f"cannot inspect registry: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise PolicyFileError("registry must not be a symbolic link")
    if not stat.S_ISREG(before.st_mode):
        raise PolicyFileError("registry must be a regular file")
    _validate_posix_owner_and_mode(
        before, require_private_read=require_private_read
    )
    if os.name == "nt":
        _validate_windows_path_security(
            policy_path.parent, require_current_owner=False
        )
        _validate_windows_path_security(
            policy_path, require_private_read=require_private_read
        )
    if before.st_size > MAX_POLICY_BYTES:
        raise PolicyFileError("registry exceeds the 128 KiB limit")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(policy_path, flags)
    except FileNotFoundError as exc:
        raise RegistryNotFoundError(f"registry does not exist: {policy_path}") from exc
    except OSError as exc:
        raise PolicyFileError(f"cannot open registry safely: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PolicyFileError("registry must remain a regular file")
        _validate_posix_owner_and_mode(
            opened, require_private_read=require_private_read
        )
        if opened.st_size > MAX_POLICY_BYTES:
            raise PolicyFileError("registry exceeds the 128 KiB limit")
        if before.st_ino and opened.st_ino and (
            before.st_dev != opened.st_dev or before.st_ino != opened.st_ino
        ):
            raise PolicyFileError("registry changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(MAX_POLICY_BYTES + 1)
    except OSError as exc:
        raise PolicyFileError(f"cannot read registry safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyFileError("registry exceeds the 128 KiB limit")
    return raw


def load_openrouter_registry(
    path: str | os.PathLike[str],
    *,
    now: int | float | datetime | None = None,
    require_fresh: bool = True,
) -> OpenRouterRegistry:
    """Load and strictly validate a regular user-owned registry file.

    A missing path raises RegistryNotFoundError. Existing but unsafe or invalid
    input raises PolicyFileError or PolicyValidationError, so integrations cannot
    accidentally treat invalid policy as an absent optional registry.
    """

    raw = _read_regular_file(path)
    return validate_openrouter_registry(
        load_json_bytes(raw), now=now, require_fresh=require_fresh
    )


def load_openmodel_registry(
    path: str | os.PathLike[str],
) -> OpenModelRegistry:
    """Load and strictly validate a private loopback open-model registry."""

    return validate_openmodel_registry(
        load_json_bytes(_read_regular_file(path, require_private_read=True))
    )


@contextmanager
def _registry_lock(target: Path, *, registry_name: str):
    """Hold one private sidecar lock across a registry compare and replacement."""

    if _ROUTE_RE.fullmatch(registry_name) is None:
        raise PolicyValidationError("registry lock name is invalid")
    parent = target.parent
    descriptor = -1
    locked = False
    try:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise PolicyFileError("registry parent must be a plain directory")
        parent_existed = parent.exists()
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent_stat = parent.stat()
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise PolicyFileError("registry parent must remain a directory")
        _validate_posix_owner_and_mode(parent_stat)
        if os.name == "nt":
            if parent_existed:
                _validate_windows_path_security(
                    parent, require_current_owner=False
                )
            else:
                _protect_windows_path(parent)

        lock_path = parent / f".{registry_name}-registry.lock"
        try:
            before = os.lstat(lock_path)
        except FileNotFoundError:
            before = None
        if before is not None:
            if stat.S_ISLNK(before.st_mode):
                raise PolicyFileError("registry lock must not be a symbolic link")
            if not stat.S_ISREG(before.st_mode):
                raise PolicyFileError("registry lock must be a regular file")
            _validate_posix_owner_and_mode(before)
            _validate_windows_path_security(lock_path)

        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PolicyFileError("registry lock must remain a regular file")
        _validate_posix_owner_and_mode(opened)
        current = os.lstat(lock_path)
        if before is not None and before.st_ino and current.st_ino and (
            before.st_dev != current.st_dev or before.st_ino != current.st_ino
        ):
            raise PolicyFileError("registry lock changed while it was opened")
        if opened.st_ino and current.st_ino and (
            opened.st_dev != current.st_dev or opened.st_ino != current.st_ino
        ):
            raise PolicyFileError("registry lock changed while it was opened")
        os.chmod(lock_path, 0o600)
        _protect_windows_path(lock_path)

        deadline = time.monotonic() + REGISTRY_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    if opened.st_size == 0:
                        os.write(descriptor, b"\0")
                        os.fsync(descriptor)
                        opened = os.fstat(descriptor)
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise RegistryBusyError(
                        "another Airlock registry operation is in progress; "
                        "try again"
                    ) from exc
                time.sleep(REGISTRY_LOCK_RETRY_SECONDS)
        locked = True
        yield
    except PolicyValidationError:
        raise
    except OSError as exc:
        raise PolicyFileError(f"cannot lock registry safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def _openrouter_registry_lock(target: Path):
    """Compatibility wrapper for the OpenRouter registry lock."""

    with _registry_lock(target, registry_name="openrouter"):
        yield


@contextmanager
def _openmodel_registry_lock(target: Path):
    """Hold the private open-model registry lock."""

    with _registry_lock(target, registry_name="openmodel"):
        yield


def _write_private_registry(
    path: str | os.PathLike[str],
    serialized: bytes,
    *,
    registry_name: str,
) -> None:
    """Atomically replace one validated private registry file."""

    if _ROUTE_RE.fullmatch(registry_name) is None:
        raise PolicyValidationError("registry temporary-file name is invalid")
    if type(serialized) is not bytes or len(serialized) > MAX_POLICY_BYTES:
        raise PolicyValidationError("serialized registry exceeds the 128 KiB limit")

    target = Path(path).expanduser()
    parent = target.parent
    require_private_read = registry_name == "openmodel"
    temporary: Path | None = None
    descriptor = -1
    before: os.stat_result | None = None
    try:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise PolicyFileError("registry parent must be a plain directory")
        parent_existed = parent.exists()
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent_stat = parent.stat()
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise PolicyFileError("registry parent must remain a directory")
        _validate_posix_owner_and_mode(parent_stat)
        if os.name == "nt":
            if parent_existed:
                _validate_windows_path_security(
                    parent, require_current_owner=False
                )
            else:
                _protect_windows_path(parent)

        try:
            before = os.lstat(target)
        except FileNotFoundError:
            before = None
        if before is not None:
            if stat.S_ISLNK(before.st_mode):
                raise PolicyFileError("registry must not be a symbolic link")
            if not stat.S_ISREG(before.st_mode):
                raise PolicyFileError("registry must be a regular file")
            _validate_posix_owner_and_mode(
                before, require_private_read=require_private_read
            )
            _validate_windows_path_security(
                target, require_private_read=require_private_read
            )

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{registry_name}-registry.", suffix=".tmp", dir=parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        _protect_windows_path(temporary)

        try:
            current = os.lstat(target)
        except FileNotFoundError:
            current = None
        if before is None and current is not None:
            raise PolicyFileError("registry appeared while it was being updated")
        if before is not None and (
            current is None
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise PolicyFileError("registry changed while it was being updated")

        os.replace(temporary, target)
        temporary = None
        _validate_windows_path_security(
            target, require_private_read=require_private_read
        )
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = -1
            try:
                directory_descriptor = os.open(parent, directory_flags)
                os.fsync(directory_descriptor)
            except OSError as exc:
                raise PolicyDurabilityError(
                    "registry was replaced, but directory durability could not be confirmed"
                ) from exc
            finally:
                if directory_descriptor >= 0:
                    try:
                        os.close(directory_descriptor)
                    except OSError as exc:
                        raise PolicyDurabilityError(
                            "registry was replaced, but directory durability could not be confirmed"
                        ) from exc
    except PolicyValidationError:
        raise
    except OSError as exc:
        raise PolicyFileError(f"cannot update registry safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def write_openrouter_registry(
    path: str | os.PathLike[str],
    value: OpenRouterRegistry | Mapping[str, Any],
    *,
    now: int | float | datetime | None = None,
    require_fresh: bool = True,
) -> OpenRouterRegistry:
    """Validate and atomically replace a private OpenRouter registry."""

    raw_value = (
        value.to_dict() if isinstance(value, OpenRouterRegistry) else dict(value)
    )
    registry = validate_openrouter_registry(
        raw_value, now=now, require_fresh=require_fresh
    )
    _write_private_registry(
        path,
        registry.canonical_bytes() + b"\n",
        registry_name="openrouter",
    )
    return registry


def write_openmodel_registry(
    path: str | os.PathLike[str],
    value: OpenModelRegistry | Mapping[str, Any],
) -> OpenModelRegistry:
    """Validate and atomically replace a private open-model registry."""

    raw_value = (
        value.to_dict() if isinstance(value, OpenModelRegistry) else dict(value)
    )
    registry = validate_openmodel_registry(raw_value)
    _write_private_registry(
        path,
        registry.canonical_bytes() + b"\n",
        registry_name="openmodel",
    )
    return registry


def compare_and_swap_openrouter_registry(
    path: str | os.PathLike[str],
    expected_digest: str | None,
    value: OpenRouterRegistry | Mapping[str, Any],
    *,
    now: int | float | datetime | None = None,
    require_fresh: bool = True,
) -> OpenRouterRegistry:
    """Replace a registry only if its validated canonical version is unchanged."""

    if expected_digest is not None and re.fullmatch(
        r"[0-9a-f]{64}\Z", expected_digest, re.ASCII
    ) is None:
        raise PolicyValidationError("expected registry digest is invalid")
    target = Path(path).expanduser()
    with _openrouter_registry_lock(target):
        try:
            current = load_openrouter_registry(
                target,
                now=now,
                require_fresh=False,
            )
        except RegistryNotFoundError:
            current_digest = None
        else:
            current_digest = current.digest()
        if current_digest != expected_digest:
            raise RegistryConflictError(
                "OpenRouter registry changed during this operation; rerun the command"
            )
        return write_openrouter_registry(
            target,
            value,
            now=now,
            require_fresh=require_fresh,
        )


def compare_and_swap_openmodel_registry(
    path: str | os.PathLike[str],
    expected_digest: str | None,
    value: OpenModelRegistry | Mapping[str, Any],
) -> OpenModelRegistry:
    """Replace the open-model registry only when its digest is unchanged."""

    if expected_digest is not None and re.fullmatch(
        r"[0-9a-f]{64}\Z", expected_digest, re.ASCII
    ) is None:
        raise PolicyValidationError("expected registry digest is invalid")
    target = Path(path).expanduser()
    with _openmodel_registry_lock(target):
        try:
            current = load_openmodel_registry(target)
        except RegistryNotFoundError:
            current_digest = None
        else:
            current_digest = current.digest()
        if current_digest != expected_digest:
            raise RegistryConflictError(
                "Open-model registry changed during this operation; rerun the command"
            )
        return write_openmodel_registry(target, value)


def _validate_exact_model(value: Any, location: str) -> str:
    model = _require_string(value, location)
    if not model.isascii() or _EXACT_MODEL_RE.fullmatch(model) is None:
        raise PolicyValidationError(
            f"{location} must be a 1-160 character exact model identifier"
        )
    return model


def _validate_snapshot_provider(value: Any, location: str) -> str:
    provider = _require_string(value, location)
    if provider not in _PROVIDERS:
        raise PolicyValidationError(
            f"{location} must be one of {sorted(_PROVIDERS)!r}"
        )
    return provider


def _validate_model_for_provider(value: Any, provider: str, location: str) -> str:
    if provider == "openrouter":
        return _validate_openrouter_model(value, location)
    if provider == "openmodel":
        model = _validate_exact_model(value, location)
        if not model.startswith("openmodel/"):
            raise PolicyValidationError(
                f"{location} is not a canonical openmodel route ID"
            )
        route = model.removeprefix("openmodel/")
        if _validate_openmodel_name(route, location) != route:
            raise PolicyValidationError(
                f"{location} is not a canonical openmodel route ID"
            )
        return model
    model = _validate_exact_model(value, location)
    prefix = {
        "anthropic": "claude-",
        "openai": "gpt-",
        "grok": "grok-",
    }[provider]
    if not model.startswith(prefix):
        raise PolicyValidationError(
            f"{location} is not a canonical {provider} model ID"
        )
    return model


def validate_session_snapshot(value: Any) -> SessionSnapshot:
    """Validate a generic, table-independent session snapshot schema v1."""

    if type(value) is not dict:
        raise PolicyValidationError("snapshot must be an object")
    # Snapshots rendered before rate-limit failover existed are still valid;
    # they simply carry no failover chains. The same applies to the
    # overflow-handoff fields added later.
    for optional_field, default in (
        ("openmodel", {"endpoints": {}, "models": {}}),
        ("failover", {}),
        ("context_windows", {}),
        ("route_categories", {}),
        ("effort_ceilings", {}),
        ("compactors", {}),
        ("overflow_shrink", "auto"),
    ):
        if optional_field not in value:
            value = {**value, optional_field: default}
    snapshot = _require_exact_fields(value, _SNAPSHOT_FIELDS, "snapshot")
    schema_version = _require_integer(snapshot["schema_version"], "schema_version")
    if schema_version != 1:
        raise PolicyValidationError("schema_version must be 1")
    protocol_version = _require_integer(
        snapshot["protocol_version"], "protocol_version", minimum=1
    )
    profile = _require_string(snapshot["profile"], "profile")
    if not (1 <= len(profile) <= 64) or _PROFILE_RE.fullmatch(profile) is None:
        raise PolicyValidationError("profile must be a 1-64 character lower-case slug")
    root_provider = _validate_snapshot_provider(
        snapshot["root_provider"], "root_provider"
    )
    root_model = _validate_model_for_provider(
        snapshot["root_model"], root_provider, "root_model"
    )

    raw_routes = snapshot["routes"]
    if type(raw_routes) is not dict:
        raise PolicyValidationError("routes must be an object")
    routes: dict[str, str] = {}
    for raw_model, raw_provider in raw_routes.items():
        provider = _validate_snapshot_provider(raw_provider, "routes provider")
        model = _validate_model_for_provider(raw_model, provider, "routes key")
        routes[model] = provider
    if root_model not in routes:
        raise PolicyValidationError("root_model does not reference a route")
    if routes[root_model] != root_provider:
        raise PolicyValidationError("root_model route disagrees with root_provider")

    raw_agents = snapshot["agents"]
    if type(raw_agents) is not dict:
        raise PolicyValidationError("agents must be an object")
    if len(raw_agents) > MAX_SNAPSHOT_AGENTS:
        raise PolicyValidationError(
            f"agents must contain at most {MAX_SNAPSHOT_AGENTS} entries"
        )
    agents: dict[str, SnapshotAgent] = {}
    referenced_models: set[str] = set()
    for raw_name, raw_agent in raw_agents.items():
        name = _require_string(raw_name, "agents key")
        if (
            not (1 <= len(name) <= 64)
            or not name.startswith("airlock-")
            or _AGENT_NAME_RE.fullmatch(name) is None
        ):
            raise PolicyValidationError(
                "agents keys must be 1-64 character airlock-* Agent names"
            )
        agent = _require_exact_fields(
            raw_agent, _AGENT_FIELDS, f"agents[{name!r}]"
        )
        model = _validate_exact_model(
            agent["model"], f"agents[{name!r}].model"
        )
        provider = _validate_snapshot_provider(
            agent["provider"], f"agents[{name!r}].provider"
        )
        extra_usage = agent["extra_usage"]
        if type(extra_usage) is not bool:
            raise PolicyValidationError(
                f"agents[{name!r}].extra_usage must be a boolean"
            )
        if provider == "openmodel":
            expected_name = f"airlock-om-{model.removeprefix('openmodel/')}"
            if name != expected_name:
                raise PolicyValidationError(
                    f"agents[{name!r}] must use its generated open-model Agent name"
                )
            if extra_usage:
                raise PolicyValidationError(
                    f"agents[{name!r}].extra_usage must be false for local compute"
                )
        if model not in routes:
            raise PolicyValidationError(
                f"agents[{name!r}].model does not reference a route"
            )
        if routes[model] != provider:
            raise PolicyValidationError(
                f"agents[{name!r}].provider disagrees with its route"
            )
        referenced_models.add(model)
        agents[name] = SnapshotAgent(
            model=model,
            provider=provider,
            extra_usage=extra_usage,
        )

    raw_openrouter = snapshot["openrouter"]
    if type(raw_openrouter) is not dict:
        raise PolicyValidationError("openrouter must be an object")
    openrouter: dict[str, SnapshotOpenRouterRoute] = {}
    for raw_model, raw_metadata in raw_openrouter.items():
        model = _validate_openrouter_model(raw_model, "openrouter key")
        if type(raw_metadata) is not dict or frozenset(raw_metadata) not in {
            _OPENROUTER_SNAPSHOT_REQUIRED_FIELDS,
            _OPENROUTER_SNAPSHOT_FIELDS,
        }:
            raise PolicyValidationError(
                f"openrouter[{model!r}] has missing or unexpected fields"
            )
        metadata = raw_metadata
        endpoint_provider = _validate_endpoint_provider(
            metadata["endpoint_provider"],
            f"openrouter[{model!r}].endpoint_provider",
        )
        provider_name = _validate_provider_name(
            metadata["provider_name"],
            f"openrouter[{model!r}].provider_name",
        )
        provider_slug = _validate_provider_slug(
            metadata["provider_slug"],
            f"openrouter[{model!r}].provider_slug",
        )
        quantization = _validate_quantization(
            metadata["quantization"],
            f"openrouter[{model!r}].quantization",
        )
        canonical_slug = _validate_openrouter_slug(
            metadata["canonical_slug"],
            f"openrouter[{model!r}].canonical_slug",
        )
        effort_ceiling = metadata.get("effort_ceiling", "high")
        if type(effort_ceiling) is not str or effort_ceiling not in _EFFORT_CEILINGS:
            raise PolicyValidationError(
                f"openrouter[{model!r}].effort_ceiling must be low, medium, high, xhigh, or max"
            )
        if routes.get(model) != "openrouter":
            raise PolicyValidationError(
                f"openrouter[{model!r}] does not reference an OpenRouter route"
            )
        openrouter[model] = SnapshotOpenRouterRoute(
            endpoint_provider=endpoint_provider,
            provider_name=provider_name,
            provider_slug=provider_slug,
            quantization=quantization,
            canonical_slug=canonical_slug,
            effort_ceiling=effort_ceiling,
            effort_ceiling_explicit="effort_ceiling" in metadata,
        )

    openrouter_routes = {
        model for model, provider in routes.items() if provider == "openrouter"
    }
    if set(openrouter) != openrouter_routes:
        missing = sorted(openrouter_routes - set(openrouter))
        raise PolicyValidationError(
            f"OpenRouter routes and metadata must agree; missing {missing!r}"
        )

    raw_openmodel = _require_exact_fields(
        snapshot["openmodel"], _OPENMODEL_SNAPSHOT_FIELDS, "openmodel"
    )
    raw_openmodel_endpoints = raw_openmodel["endpoints"]
    if type(raw_openmodel_endpoints) is not dict:
        raise PolicyValidationError("openmodel.endpoints must be an object")
    if len(raw_openmodel_endpoints) > MAX_OPENMODEL_ENDPOINTS:
        raise PolicyValidationError(
            f"openmodel.endpoints must contain at most {MAX_OPENMODEL_ENDPOINTS} entries"
        )
    openmodel_endpoints: dict[str, SnapshotOpenModelEndpoint] = {}
    openmodel_urls: set[str] = set()
    for raw_endpoint_id, raw_metadata in raw_openmodel_endpoints.items():
        endpoint_id = _validate_openmodel_name(
            raw_endpoint_id, "openmodel.endpoints key"
        )
        metadata = _require_exact_fields(
            raw_metadata,
            _OPENMODEL_ENDPOINT_SNAPSHOT_FIELDS,
            f"openmodel.endpoints[{endpoint_id!r}]",
        )
        base_url = validate_openmodel_base_url(
            metadata["base_url"],
            f"openmodel.endpoints[{endpoint_id!r}].base_url",
        )
        trust = _require_string(
            metadata["trust"], f"openmodel.endpoints[{endpoint_id!r}].trust"
        )
        protocol = _require_string(
            metadata["protocol"],
            f"openmodel.endpoints[{endpoint_id!r}].protocol",
        )
        auth = _require_string(
            metadata["auth"], f"openmodel.endpoints[{endpoint_id!r}].auth"
        )
        if trust != _OPENMODEL_TRUST:
            raise PolicyValidationError(
                f"openmodel.endpoints[{endpoint_id!r}].trust must be loopback"
            )
        if protocol != _OPENMODEL_PROTOCOL:
            raise PolicyValidationError(
                f"openmodel.endpoints[{endpoint_id!r}].protocol must be "
                f"{_OPENMODEL_PROTOCOL}"
            )
        if auth != _OPENMODEL_AUTH:
            raise PolicyValidationError(
                f"openmodel.endpoints[{endpoint_id!r}].auth must be none"
            )
        max_concurrency = _require_integer(
            metadata["max_concurrency"],
            f"openmodel.endpoints[{endpoint_id!r}].max_concurrency",
            minimum=1,
        )
        if max_concurrency > 64:
            raise PolicyValidationError(
                f"openmodel.endpoints[{endpoint_id!r}].max_concurrency must be at most 64"
            )
        if base_url in openmodel_urls:
            raise PolicyValidationError("duplicate open-model endpoint URL")
        openmodel_urls.add(base_url)
        openmodel_endpoints[endpoint_id] = SnapshotOpenModelEndpoint(
            base_url=base_url,
            trust=trust,
            protocol=protocol,
            auth=auth,
            max_concurrency=max_concurrency,
        )

    raw_openmodel_models = raw_openmodel["models"]
    if type(raw_openmodel_models) is not dict:
        raise PolicyValidationError("openmodel.models must be an object")
    if len(raw_openmodel_models) > MAX_OPENMODEL_MODELS:
        raise PolicyValidationError(
            f"openmodel.models must contain at most {MAX_OPENMODEL_MODELS} entries"
        )
    openmodel_models: dict[str, SnapshotOpenModelRoute] = {}
    openmodel_request_bindings: set[tuple[str, str]] = set()
    openmodel_response_bindings: set[tuple[str, str]] = set()
    referenced_openmodel_endpoints: set[str] = set()
    for raw_model, raw_metadata in raw_openmodel_models.items():
        model = _validate_model_for_provider(
            raw_model, "openmodel", "openmodel.models key"
        )
        metadata = _require_exact_fields(
            raw_metadata,
            _OPENMODEL_MODEL_SNAPSHOT_FIELDS,
            f"openmodel.models[{model!r}]",
        )
        endpoint_id = _validate_openmodel_name(
            metadata["endpoint"], f"openmodel.models[{model!r}].endpoint"
        )
        if endpoint_id not in openmodel_endpoints:
            raise PolicyValidationError(
                f"openmodel.models[{model!r}].endpoint is not declared"
            )
        upstream_model = validate_openmodel_identity(
            metadata["upstream_model"],
            f"openmodel.models[{model!r}].upstream_model",
        )
        accepted = _validate_openmodel_identities(
            metadata["accepted_response_models"],
            f"openmodel.models[{model!r}].accepted_response_models",
        )
        context_window = _require_integer(
            metadata["context_window"],
            f"openmodel.models[{model!r}].context_window",
        )
        if not MIN_CONTEXT_WINDOW_TOKENS <= context_window <= MAX_CONTEXT_WINDOW_TOKENS:
            raise PolicyValidationError(
                f"openmodel.models[{model!r}].context_window is outside the supported range"
            )
        max_output_tokens = _require_integer(
            metadata["max_output_tokens"],
            f"openmodel.models[{model!r}].max_output_tokens",
            minimum=1,
        )
        if max_output_tokens > context_window:
            raise PolicyValidationError(
                f"openmodel.models[{model!r}].max_output_tokens exceeds its context window"
            )
        streaming = metadata["streaming"]
        if type(streaming) is not bool:
            raise PolicyValidationError(
                f"openmodel.models[{model!r}].streaming must be a boolean"
            )
        tools = _validate_openmodel_tool_support(
            metadata["tools"], f"openmodel.models[{model!r}].tools"
        )
        tool_choice = _validate_openmodel_tool_choices(
            metadata["tool_choice"],
            f"openmodel.models[{model!r}].tool_choice",
            tool_support=tools,
        )
        if routes.get(model) != "openmodel":
            raise PolicyValidationError(
                f"openmodel.models[{model!r}] does not reference an openmodel route"
            )
        request_binding = (endpoint_id, upstream_model)
        response_bindings = {(endpoint_id, identity) for identity in accepted}
        if request_binding in openmodel_request_bindings:
            raise PolicyValidationError(
                "duplicate open-model endpoint and upstream model binding"
            )
        if response_bindings & openmodel_response_bindings:
            raise PolicyValidationError(
                "open-model response identity is ambiguous on its endpoint"
            )
        openmodel_request_bindings.add(request_binding)
        openmodel_response_bindings.update(response_bindings)
        referenced_openmodel_endpoints.add(endpoint_id)
        openmodel_models[model] = SnapshotOpenModelRoute(
            endpoint=endpoint_id,
            upstream_model=upstream_model,
            accepted_response_models=accepted,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            streaming=streaming,
            tools=tools,
            tool_choice=tool_choice,
        )

    openmodel_routes = {
        model for model, provider in routes.items() if provider == "openmodel"
    }
    if set(openmodel_models) != openmodel_routes:
        missing = sorted(openmodel_routes - set(openmodel_models))
        raise PolicyValidationError(
            f"openmodel routes and metadata must agree; missing {missing!r}"
        )
    if set(openmodel_endpoints) != referenced_openmodel_endpoints:
        raise PolicyValidationError(
            "openmodel snapshot endpoints must be referenced exactly"
        )

    raw_failover = snapshot["failover"]
    if type(raw_failover) is not dict:
        raise PolicyValidationError("failover must be an object")
    if len(raw_failover) > MAX_SNAPSHOT_FAILOVER_ENTRIES:
        raise PolicyValidationError(
            f"failover must contain at most {MAX_SNAPSHOT_FAILOVER_ENTRIES} entries"
        )
    failover: dict[str, tuple[str, ...]] = {}
    for raw_model, raw_peers in raw_failover.items():
        if type(raw_model) is not str or not raw_model:
            raise PolicyValidationError("failover keys must be model IDs")
        if raw_model not in routes:
            raise PolicyValidationError(
                f"failover key {raw_model!r} does not reference a route"
            )
        if routes[raw_model] == "openmodel":
            raise PolicyValidationError(
                "openmodel routes must not appear in failover chains"
            )
        if type(raw_peers) is not list or not (
            1 <= len(raw_peers) <= MAX_SNAPSHOT_FAILOVER_PEERS
        ):
            raise PolicyValidationError(
                f"failover[{raw_model!r}] must list 1-{MAX_SNAPSHOT_FAILOVER_PEERS} model IDs"
            )
        peers: list[str] = []
        for raw_peer in raw_peers:
            if type(raw_peer) is not str or not raw_peer:
                raise PolicyValidationError(
                    f"failover[{raw_model!r}] entries must be model IDs"
                )
            if raw_peer == raw_model:
                raise PolicyValidationError(
                    f"failover[{raw_model!r}] must not chain to itself"
                )
            if raw_peer in peers:
                raise PolicyValidationError(
                    f"failover[{raw_model!r}] repeats a peer"
                )
            if raw_peer not in routes:
                raise PolicyValidationError(
                    f"failover[{raw_model!r}] references unknown route {raw_peer!r}"
                )
            if routes[raw_peer] == "openmodel":
                raise PolicyValidationError(
                    "openmodel routes must not appear in failover chains"
                )
            peers.append(raw_peer)
        failover[raw_model] = tuple(peers)

    raw_windows = snapshot["context_windows"]
    if type(raw_windows) is not dict:
        raise PolicyValidationError("context_windows must be an object")
    if len(raw_windows) > MAX_SNAPSHOT_ROUTE_ENTRIES:
        raise PolicyValidationError(
            f"context_windows must contain at most {MAX_SNAPSHOT_ROUTE_ENTRIES} entries"
        )
    context_windows: dict[str, int] = {}
    for raw_model, raw_window in raw_windows.items():
        if type(raw_model) is not str or not raw_model:
            raise PolicyValidationError("context_windows keys must be model IDs")
        if raw_model not in routes:
            raise PolicyValidationError(
                f"context_windows key {raw_model!r} does not reference a route"
            )
        window = _require_integer(raw_window, f"context_windows[{raw_model!r}]")
        if not MIN_CONTEXT_WINDOW_TOKENS <= window <= MAX_CONTEXT_WINDOW_TOKENS:
            raise PolicyValidationError(
                f"context_windows[{raw_model!r}] must be between "
                f"{MIN_CONTEXT_WINDOW_TOKENS} and {MAX_CONTEXT_WINDOW_TOKENS}"
            )
        context_windows[raw_model] = window

    for model, metadata in openmodel_models.items():
        if context_windows.get(model) != metadata.context_window:
            raise PolicyValidationError(
                f"context_windows[{model!r}] must match openmodel metadata"
            )

    raw_categories = snapshot["route_categories"]
    if type(raw_categories) is not dict:
        raise PolicyValidationError("route_categories must be an object")
    if len(raw_categories) > MAX_SNAPSHOT_ROUTE_ENTRIES:
        raise PolicyValidationError(
            f"route_categories must contain at most {MAX_SNAPSHOT_ROUTE_ENTRIES} entries"
        )
    route_categories: dict[str, str] = {}
    for raw_model, raw_category in raw_categories.items():
        if type(raw_model) is not str or raw_model not in routes:
            raise PolicyValidationError(
                "route_categories keys must reference routed model IDs"
            )
        if type(raw_category) is not str or raw_category not in _ROUTE_CATEGORIES:
            raise PolicyValidationError(
                f"route_categories[{raw_model!r}] must be included, extra, metered, or unknown"
            )
        route_categories[raw_model] = raw_category

    raw_effort_ceilings = snapshot["effort_ceilings"]
    if type(raw_effort_ceilings) is not dict:
        raise PolicyValidationError("effort_ceilings must be an object")
    if len(raw_effort_ceilings) > MAX_SNAPSHOT_ROUTE_ENTRIES:
        raise PolicyValidationError(
            f"effort_ceilings must contain at most {MAX_SNAPSHOT_ROUTE_ENTRIES} entries"
        )
    effort_ceilings: dict[str, str] = {}
    for raw_model, raw_ceiling in raw_effort_ceilings.items():
        if type(raw_model) is not str or raw_model not in routes:
            raise PolicyValidationError(
                "effort_ceilings keys must reference routed model IDs"
            )
        if type(raw_ceiling) is not str or raw_ceiling not in _EFFORT_CEILINGS:
            raise PolicyValidationError(
                f"effort_ceilings[{raw_model!r}] must be low, medium, high, xhigh, or max"
            )
        effort_ceilings[raw_model] = raw_ceiling

    raw_compactors = snapshot["compactors"]
    if type(raw_compactors) is not dict:
        raise PolicyValidationError("compactors must be an object")
    compactors: dict[str, str] = {}
    for raw_provider, raw_model in raw_compactors.items():
        if (
            type(raw_provider) is not str
            or raw_provider not in _PROVIDERS
            or raw_provider in {"openrouter", "openmodel"}
        ):
            raise PolicyValidationError(
                "compactors keys must be anthropic, openai, or grok providers"
            )
        if type(raw_model) is not str or raw_model not in routes:
            raise PolicyValidationError(
                f"compactors[{raw_provider!r}] does not reference a route"
            )
        if routes[raw_model] != raw_provider:
            raise PolicyValidationError(
                f"compactors[{raw_provider!r}] disagrees with its route provider"
            )
        compactors[raw_provider] = raw_model

    overflow_shrink = snapshot["overflow_shrink"]
    if type(overflow_shrink) is not str or overflow_shrink not in VALID_OVERFLOW_SHRINK_MODES:
        raise PolicyValidationError(
            "overflow_shrink must be auto, truncate, summarize, or off"
        )

    canonical_references = referenced_models | {root_model}
    wire_aliases = {
        model.removesuffix("[1m]")
        for model in canonical_references
        if model.endswith("[1m]")
        and model.removesuffix("[1m]") in routes
        and routes[model.removesuffix("[1m]")] == routes[model]
    }
    unreferenced = set(routes) - canonical_references - wire_aliases
    if unreferenced:
        raise PolicyValidationError(
            f"snapshot contains unreferenced non-root routes: {sorted(unreferenced)!r}"
        )

    result = SessionSnapshot(
        schema_version=schema_version,
        protocol_version=protocol_version,
        profile=profile,
        root_model=root_model,
        root_provider=root_provider,
        routes=MappingProxyType(dict(routes)),
        agents=MappingProxyType(dict(agents)),
        openrouter=MappingProxyType(dict(openrouter)),
        openmodel=SnapshotOpenModel(
            endpoints=MappingProxyType(dict(openmodel_endpoints)),
            models=MappingProxyType(dict(openmodel_models)),
        ),
        failover=MappingProxyType(failover),
        context_windows=MappingProxyType(context_windows),
        route_categories=MappingProxyType(route_categories),
        effort_ceilings=MappingProxyType(effort_ceilings),
        compactors=MappingProxyType(compactors),
        overflow_shrink=overflow_shrink,
    )
    if len(result.canonical_bytes()) > MAX_POLICY_BYTES:
        raise PolicyValidationError("canonical snapshot exceeds the 128 KiB limit")
    return result


def load_session_snapshot_bytes(raw: bytes) -> SessionSnapshot:
    """Decode and validate bounded duplicate-free session snapshot JSON bytes."""

    return validate_session_snapshot(load_json_bytes(raw))


def load_session_snapshot(
    path: str | os.PathLike[str], expected_digest: str
) -> SessionSnapshot:
    """Load a safe snapshot and require its canonical digest to match."""

    if (
        type(expected_digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_digest, re.ASCII) is None
    ):
        raise PolicyValidationError("session snapshot digest is invalid")
    try:
        raw = _read_regular_file(path)
    except RegistryNotFoundError as exc:
        raise PolicyFileError("session snapshot does not exist") from exc
    snapshot = load_session_snapshot_bytes(raw)
    if snapshot.digest() != expected_digest:
        raise PolicyValidationError("session snapshot digest does not match")
    return snapshot
