#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Manage Airlock's user-owned OpenRouter model registry."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable
from urllib import error, parse, request

import airlock_policy as policy
import airlock_openrouter_presets as presets

OPENROUTER_API_ORIGIN = "https://openrouter.ai"
OPENROUTER_API_BASE = f"{OPENROUTER_API_ORIGIN}/api/v1"
MAX_CATALOG_BYTES = 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 20
REQUIRED_PARAMETERS = frozenset({"tools", "tool_choice"})


class ModelsError(RuntimeError):
    """Raised when a registry-management operation cannot complete safely."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ModelsError(f"{message}\n{self.format_usage().strip()}")


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class CatalogSelection:
    model: str
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    canonical_slug: str
    supported_parameters: tuple[str, ...]
    expiration_date: str | None

    def entry(self, route: str, checked_at: int, enabled: bool = True) -> dict[str, Any]:
        return {
            "route": route,
            "model": self.model,
            "endpoint_provider": self.endpoint_provider,
            "provider_name": self.provider_name,
            "provider_slug": self.provider_slug,
            "quantization": self.quantization,
            "canonical_slug": self.canonical_slug,
            "alias_target": None,
            "supported_parameters": list(self.supported_parameters),
            "expiration_date": self.expiration_date,
            "checked_at": checked_at,
            "enabled": enabled,
        }


def _object(value: Any, location: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ModelsError(f"OpenRouter catalog {location} is not an object")
    return value


def _array(value: Any, location: str) -> list[Any]:
    if type(value) is not list:
        raise ModelsError(f"OpenRouter catalog {location} is not an array")
    return value


def _string(value: Any, location: str) -> str:
    if type(value) is not str or not value:
        raise ModelsError(f"OpenRouter catalog {location} is not a non-empty string")
    return value


def _parameters(value: Any, location: str) -> tuple[str, ...]:
    items = _array(value, location)
    parameters: list[str] = []
    for index, item in enumerate(items):
        parameters.append(_string(item, f"{location}[{index}]"))
    result = tuple(sorted(set(parameters)))
    missing = REQUIRED_PARAMETERS - set(result)
    if missing:
        raise ModelsError(
            f"OpenRouter catalog {location} is missing required parameters: "
            f"{sorted(missing)!r}"
        )
    return result


def _catalog_url(path: str) -> str:
    url = f"{OPENROUTER_API_BASE}{path}"
    parsed = parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ModelsError("OpenRouter catalog URL is not pinned to openrouter.ai")
    return url


def fetch_catalog_json(
    path: str,
    *,
    opener: Any | None = None,
    timeout: int = NETWORK_TIMEOUT_SECONDS,
) -> Any:
    """Fetch one bounded public OpenRouter catalog response without credentials."""

    target = _catalog_url(path)
    client = opener or request.build_opener(NoRedirectHandler())
    catalog_request = request.Request(
        target,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "Airlock OpenRouter registry",
        },
        method="GET",
    )
    try:
        response = client.open(catalog_request, timeout=timeout)
        with response:
            status = getattr(response, "status", response.getcode())
            if status != 200:
                raise ModelsError(f"OpenRouter catalog returned HTTP {status}")
            content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
            if content_encoding not in {"", "identity"}:
                raise ModelsError("OpenRouter catalog returned compressed content")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "application/problem+json"}:
                raise ModelsError("OpenRouter catalog returned a non-JSON response")
            length_text = response.headers.get("Content-Length")
            if length_text:
                try:
                    length = int(length_text)
                except ValueError as exc:
                    raise ModelsError("OpenRouter catalog returned an invalid length") from exc
                if length < 0 or length > MAX_CATALOG_BYTES:
                    raise ModelsError("OpenRouter catalog response exceeds its size limit")
            raw = response.read(MAX_CATALOG_BYTES + 1)
    except ModelsError:
        raise
    except error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ModelsError("OpenRouter catalog redirected unexpectedly") from exc
        raise ModelsError(f"OpenRouter catalog returned HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ModelsError("OpenRouter catalog request failed") from exc
    if len(raw) > MAX_CATALOG_BYTES:
        raise ModelsError("OpenRouter catalog response exceeds its size limit")
    try:
        return policy.load_json_bytes(raw, max_bytes=MAX_CATALOG_BYTES)
    except policy.PolicyValidationError as exc:
        raise ModelsError(f"OpenRouter catalog response is invalid: {exc}") from exc


def _validate_requested_tokens(route: str, model: str, endpoint_provider: str, now: int) -> None:
    candidate = {
        "schema_version": 1,
        "models": [{
            "route": route,
            "model": model,
            "endpoint_provider": endpoint_provider,
            "provider_name": "Catalog Check",
            "provider_slug": "catalog-check",
            "quantization": "unknown",
            "canonical_slug": model,
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": now,
            "enabled": True,
        }],
    }
    try:
        policy.validate_openrouter_registry(candidate, now=now)
    except policy.PolicyValidationError as exc:
        raise ModelsError(str(exc)) from exc


def verify_catalog_selection(
    model: str,
    endpoint_provider: str,
    *,
    fetch: Callable[[str], Any] = fetch_catalog_json,
) -> CatalogSelection:
    """Verify one exact routable model and one exact serving endpoint."""

    owner, slug = model.split("/", 1)
    owner_path = parse.quote(owner, safe="")
    slug_path = parse.quote(slug, safe="")
    model_payload = _object(
        fetch(f"/model/{owner_path}/{slug_path}"), "model response"
    )
    model_data = _object(model_payload.get("data"), "model data")
    catalog_id = _string(model_data.get("id"), "model id")
    canonical_slug = _string(
        model_data.get("canonical_slug"), "canonical slug"
    )
    if catalog_id != model:
        raise ModelsError(
            "OpenRouter catalog did not return the requested routable model"
        )
    if (
        "alias_target" in model_data
        and model_data["alias_target"] is not None
    ):
        raise ModelsError("OpenRouter catalog model declares an alias target")
    model_parameters = _parameters(
        model_data.get("supported_parameters"), "model supported_parameters"
    )
    expiration_date = model_data.get("expiration_date")
    if expiration_date is not None and type(expiration_date) is not str:
        raise ModelsError("OpenRouter catalog expiration_date is invalid")

    endpoint_payload = _object(
        fetch(f"/models/{owner_path}/{slug_path}/endpoints"),
        "endpoint response",
    )
    endpoint_data = _object(endpoint_payload.get("data"), "endpoint data")
    endpoint_catalog_id = _string(endpoint_data.get("id"), "endpoint model id")
    if endpoint_catalog_id != model:
        raise ModelsError(
            "OpenRouter endpoint catalog did not return the requested routable model"
        )
    endpoints = _array(endpoint_data.get("endpoints"), "endpoints")
    selected: dict[str, Any] | None = None
    for index, raw_endpoint in enumerate(endpoints):
        endpoint = _object(raw_endpoint, f"endpoints[{index}]")
        tag = endpoint.get("tag")
        if tag == endpoint_provider:
            if selected is not None:
                raise ModelsError("OpenRouter catalog returned a duplicate endpoint tag")
            selected = endpoint
    if selected is None:
        raise ModelsError("OpenRouter catalog does not list the requested endpoint provider")
    if _string(selected.get("model_id"), "endpoint model_id") != model:
        raise ModelsError("OpenRouter endpoint does not serve the requested exact model")
    provider_name = _string(
        selected.get("provider_name"), "endpoint provider_name"
    )
    quantization = _string(
        selected.get("quantization"), "endpoint quantization"
    )
    provider_payload = _object(fetch("/providers"), "provider response")
    provider_rows = _array(provider_payload.get("data"), "providers")
    provider_slug: str | None = None
    for index, raw_provider in enumerate(provider_rows):
        provider = _object(raw_provider, f"providers[{index}]")
        if provider.get("name") != provider_name:
            continue
        if provider_slug is not None:
            raise ModelsError(
                "OpenRouter provider registry returned a duplicate provider name"
            )
        provider_slug = _string(provider.get("slug"), "provider slug")
    if provider_slug is None:
        raise ModelsError(
            "OpenRouter provider registry does not list the endpoint provider"
        )
    equivalent_routes = sum(
        1
        for raw_endpoint in endpoints
        if type(raw_endpoint) is dict
        and raw_endpoint.get("provider_name") == provider_name
        and raw_endpoint.get("quantization") == quantization
    )
    if equivalent_routes != 1:
        raise ModelsError(
            "OpenRouter endpoint cannot be pinned uniquely by provider and quantization"
        )
    endpoint_parameters = _parameters(
        selected.get("supported_parameters"),
        "endpoint supported_parameters",
    )
    supported_parameters = tuple(
        sorted(set(model_parameters) & set(endpoint_parameters))
    )
    missing = REQUIRED_PARAMETERS - set(supported_parameters)
    if missing:
        raise ModelsError(
            "OpenRouter model and endpoint do not share required tool parameters"
        )
    selection = CatalogSelection(
        model=model,
        endpoint_provider=endpoint_provider,
        provider_name=provider_name,
        provider_slug=provider_slug,
        quantization=quantization,
        canonical_slug=canonical_slug,
        supported_parameters=supported_parameters,
        expiration_date=expiration_date,
    )
    try:
        policy.validate_openrouter_registry(
            {
                "schema_version": 1,
                "models": [selection.entry("catalog-check", int(time.time()))],
            }
        )
    except policy.PolicyValidationError as exc:
        raise ModelsError(f"OpenRouter catalog metadata is invalid: {exc}") from exc
    return selection


def load_registry_version(
    path: Path,
    *,
    require_fresh: bool,
) -> tuple[policy.OpenRouterRegistry, str | None]:
    try:
        registry = policy.load_openrouter_registry(
            path,
            require_fresh=require_fresh,
        )
    except policy.RegistryNotFoundError:
        return policy.OpenRouterRegistry(schema_version=1, models=()), None
    except policy.PolicyValidationError as exc:
        raise ModelsError(f"OpenRouter registry is invalid: {exc}") from exc
    return registry, registry.digest()


def load_registry(path: Path, *, require_fresh: bool) -> policy.OpenRouterRegistry:
    registry, _digest = load_registry_version(
        path,
        require_fresh=require_fresh,
    )
    return registry


def commit_registry_update(
    path: Path,
    expected_digest: str | None,
    value: dict[str, Any],
    *,
    now: int | None = None,
    require_fresh: bool = True,
) -> None:
    try:
        policy.compare_and_swap_openrouter_registry(
            path,
            expected_digest,
            value,
            now=now,
            require_fresh=require_fresh,
        )
    except (policy.RegistryConflictError, policy.RegistryBusyError) as exc:
        raise ModelsError(str(exc)) from exc
    except policy.PolicyValidationError as exc:
        raise ModelsError(f"OpenRouter registry update is invalid: {exc}") from exc


def confirm(question: str, *, approved: bool, input_stream: Any | None = None) -> None:
    if approved:
        return
    stream = input_stream or sys.stdin
    if not stream.isatty():
        raise ModelsError("confirmation is required; rerun with --yes")
    print(f"{question} [y/N] ", end="", flush=True)
    answer = stream.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise ModelsError("operation cancelled")


def list_models(path: Path) -> int:
    registry = load_registry(path, require_fresh=False)
    if not registry.models:
        print("No OpenRouter models are declared.")
        return 0
    print(
        "ROUTE\tMODEL\tENDPOINT\tPROVIDER\tPROVIDER_SLUG\tQUANTIZATION\t"
        "ENABLED\tCHECKED_AT\tEXPIRATION"
    )
    for entry in registry.models:
        print(
            f"{entry.route}\t{entry.model}\t{entry.endpoint_provider}\t"
            f"{entry.provider_name}\t{entry.provider_slug}\t"
            f"{entry.quantization}\t"
            f"{'yes' if entry.enabled else 'no'}\t{entry.checked_at}\t"
            f"{entry.expiration_date or '-'}"
        )
    return 0


def list_presets() -> int:
    print("Curated OpenRouter presets are opt-in and are not enabled by setup.")
    print(
        "Suggested uses and tradeoffs are community-derived, unverified, "
        "and not capability, price, or availability guarantees."
    )
    for preset in presets.PRESETS:
        print(
            f"{preset.name}\t{preset.model}\t{preset.endpoint_provider}\t"
            f"{preset.provider_name}\t{preset.provider_slug}\t"
            f"{preset.quantization}\t"
            f"evidence {preset.evidence_date}"
        )
        print(f"  Suggested use: {preset.suggested_use}")
        print(f"  Tradeoffs: {preset.tradeoffs}")
    return 0


def doctor_status(path: Path, *, now_fn: Callable[[], float] = time.time) -> int:
    """Print a sanitized, offline registry summary for Doctor."""

    try:
        registry = policy.load_openrouter_registry(
            path,
            now=int(now_fn()),
            require_fresh=False,
        )
    except policy.RegistryNotFoundError:
        print("STATE=absent")
        return 0
    except policy.PolicyValidationError as exc:
        print("STATE=invalid")
        print(f"DETAIL={exc}")
        return 2

    state = "valid"
    detail = ""
    try:
        policy.validate_openrouter_registry(
            registry.to_dict(),
            now=int(now_fn()),
            require_fresh=True,
        )
    except policy.PolicyValidationError as exc:
        state = "stale"
        detail = str(exc)

    print(f"STATE={state}")
    print(f"COUNT={len(registry.models)}")
    for entry in registry.models:
        enabled = "enabled" if entry.enabled else "disabled"
        print(
            f"MODEL={entry.route}\t{entry.model}\t"
            f"{entry.endpoint_provider}\t{enabled}"
        )
    if detail:
        print(f"DETAIL={detail}")
    return 0


def add_model(
    path: Path,
    route: str,
    model: str,
    endpoint_provider: str,
    *,
    approved: bool,
    now_fn: Callable[[], float] = time.time,
    verify: Callable[[str, str], CatalogSelection] = verify_catalog_selection,
) -> int:
    now = int(now_fn())
    _validate_requested_tokens(route, model, endpoint_provider, now)
    registry, expected_digest = load_registry_version(
        path,
        require_fresh=True,
    )
    if len(registry.models) >= policy.MAX_OPENROUTER_ENTRIES:
        raise ModelsError(
            f"OpenRouter registry already has {policy.MAX_OPENROUTER_ENTRIES} entries"
        )
    if any(entry.route == route for entry in registry.models):
        raise ModelsError(f"OpenRouter route already exists: {route}")
    if any(entry.model == model for entry in registry.models):
        raise ModelsError(f"OpenRouter model already exists: {model}")
    confirm(
        "Fetch public model and endpoint metadata from openrouter.ai?",
        approved=approved,
    )
    selection = verify(model, endpoint_provider)
    value = registry.to_dict()
    value["models"].append(selection.entry(route, now))
    commit_registry_update(
        path,
        expected_digest,
        value,
        now=now,
    )
    print(f"Added airlock-or-{route}: {model} via {endpoint_provider}")
    return 0


def add_preset(
    path: Path,
    name: str,
    *,
    approved: bool,
    now_fn: Callable[[], float] = time.time,
    verify: Callable[[str, str], CatalogSelection] = verify_catalog_selection,
) -> int:
    preset = presets.preset_by_name(name)
    if preset is None:
        valid = ", ".join(item.name for item in presets.PRESETS)
        raise ModelsError(
            f"unknown OpenRouter preset: {name}; valid presets: {valid}"
        )

    def verify_preset(model: str, endpoint_provider: str) -> CatalogSelection:
        selection = verify(model, endpoint_provider)
        if (
            selection.model != preset.model
            or selection.endpoint_provider != preset.endpoint_provider
            or selection.provider_name != preset.provider_name
            or selection.provider_slug != preset.provider_slug
            or selection.quantization != preset.quantization
        ):
            raise ModelsError(
                f"{preset.name}: OpenRouter preset identity or endpoint changed"
            )
        if selection.canonical_slug != preset.canonical_slug:
            raise ModelsError(
                f"{preset.name}: OpenRouter now maps {preset.model} to a "
                "different canonical model; update Airlock before adding "
                "this preset"
            )
        return selection

    return add_model(
        path,
        preset.route,
        preset.model,
        preset.endpoint_provider,
        approved=approved,
        now_fn=now_fn,
        verify=verify_preset,
    )


def remove_model(path: Path, route: str, *, approved: bool) -> int:
    registry, expected_digest = load_registry_version(
        path,
        require_fresh=False,
    )
    selected = [entry for entry in registry.models if entry.route == route]
    if not selected:
        raise ModelsError(f"OpenRouter route does not exist: {route}")
    confirm(f"Remove OpenRouter route {route}?", approved=approved)
    value = registry.to_dict()
    value["models"] = [
        entry for entry in value["models"] if entry["route"] != route
    ]
    commit_registry_update(
        path,
        expected_digest,
        value,
        require_fresh=False,
    )
    print(f"Removed airlock-or-{route}.")
    return 0


def refresh_models(
    path: Path,
    route: str | None,
    *,
    approved: bool,
    apply: bool,
    now_fn: Callable[[], float] = time.time,
    verify: Callable[[str, str], CatalogSelection] = verify_catalog_selection,
) -> int:
    registry, expected_digest = load_registry_version(
        path,
        require_fresh=False,
    )
    entries = [
        entry for entry in registry.models if route is None or entry.route == route
    ]
    if not entries:
        if route is not None:
            raise ModelsError(f"OpenRouter route does not exist: {route}")
        raise ModelsError("OpenRouter registry has no models to refresh")
    now = int(now_fn())
    if apply and route is not None:
        stale_routes: list[str] = []
        for other in registry.models:
            if other.route == route:
                continue
            try:
                policy.validate_openrouter_registry(
                    {
                        "schema_version": 1,
                        "models": [other.to_dict()],
                    },
                    now=now,
                )
            except policy.PolicyValidationError:
                stale_routes.append(other.route)
        if stale_routes:
            raise ModelsError(
                "cannot apply a targeted refresh while other routes are stale: "
                f"{', '.join(stale_routes)}; refresh all routes with "
                "airlock openrouter models refresh --apply, or remove the "
                "stale routes"
            )
    confirm(
        "Fetch public model and endpoint metadata from openrouter.ai?",
        approved=approved,
    )
    updates: dict[str, dict[str, Any]] = {}
    messages: list[str] = []
    for entry in entries:
        selection = verify(entry.model, entry.endpoint_provider)
        if selection.canonical_slug != entry.canonical_slug:
            raise ModelsError(
                f"{entry.route}: OpenRouter now maps {entry.model} to a "
                "different canonical model; remove and re-add the route "
                "to accept the change"
            )
        if (
            selection.provider_name != entry.provider_name
            or selection.provider_slug != entry.provider_slug
            or selection.quantization != entry.quantization
        ):
            raise ModelsError(
                f"{entry.route}: OpenRouter endpoint routing metadata changed; "
                "remove and re-add the route to accept the change"
            )
        updated = selection.entry(entry.route, now, enabled=entry.enabled)
        updates[entry.route] = updated
        changed = [
            field
            for field in (
                "supported_parameters",
                "expiration_date",
            )
            if updated[field] != entry.to_dict()[field]
        ]
        if changed:
            messages.append(f"{entry.route}: metadata changed ({', '.join(changed)})")
        else:
            messages.append(f"{entry.route}: model and endpoint still match")
    if not apply:
        for message in messages:
            print(message)
        print("Registry not changed. Rerun with --apply to save verified metadata.")
        return 0
    value = registry.to_dict()
    value["models"] = [updates.get(entry["route"], entry) for entry in value["models"]]
    commit_registry_update(
        path,
        expected_digest,
        value,
        now=now,
    )
    for message in messages:
        print(message)
    print("OpenRouter registry metadata updated.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="airlock openrouter models")
    parser.add_argument("--registry", required=True, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list")
    commands.add_parser("presets")

    add = commands.add_parser("add")
    add.add_argument("route")
    add.add_argument("model")
    add.add_argument("endpoint_provider")
    add.add_argument("--yes", action="store_true")

    add_preset_parser = commands.add_parser("add-preset")
    add_preset_parser.add_argument("name")
    add_preset_parser.add_argument("--yes", action="store_true")

    remove = commands.add_parser("remove")
    remove.add_argument("route")
    remove.add_argument("--yes", action="store_true")

    refresh = commands.add_parser("refresh")
    refresh.add_argument("route", nargs="?")
    refresh.add_argument("--apply", action="store_true")
    refresh.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) == 3
        and arguments[0] == "--registry"
        and arguments[2] == "_doctor-status"
    ):
        try:
            return doctor_status(Path(arguments[1]).expanduser())
        except policy.PolicyValidationError as exc:
            print(f"airlock openrouter models: {exc}", file=sys.stderr)
            return 2
    try:
        args = build_parser().parse_args(arguments)
        path = Path(args.registry).expanduser()
        if args.action == "list":
            return list_models(path)
        if args.action == "presets":
            return list_presets()
        if args.action == "add":
            return add_model(
                path,
                args.route,
                args.model,
                args.endpoint_provider,
                approved=args.yes,
            )
        if args.action == "add-preset":
            return add_preset(
                path,
                args.name,
                approved=args.yes,
            )
        if args.action == "remove":
            return remove_model(path, args.route, approved=args.yes)
        if args.action == "refresh":
            return refresh_models(
                path,
                args.route,
                approved=args.yes,
                apply=args.apply,
            )
        raise ModelsError("unsupported OpenRouter models action")
    except (ModelsError, policy.PolicyValidationError) as exc:
        print(f"airlock openrouter models: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
