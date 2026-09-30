"""Read-only RabbitMQ ``SpaceValue`` inventory integration.

The vendor FRS confirms the functional request/response flow and the use of
1-m inventory segments, but the production RabbitMQ ICD is not available.
Consequently this module implements the transport, safety boundaries and a
strict *configured* JSON codec without hard-coding any unevidenced exchange,
routing key or wire-field name.

Credentials never enter a dataclass, Scenario, UI widget or export.  A site
configuration contains only the name of an environment variable holding the
AMQP URL.  The optional Pika dependency is imported only when a real probe or
fetch is requested, so the offline simulator remains dependency-free.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Optional,
                    Protocol, Sequence, Tuple)
from urllib.parse import urlsplit

from .domain import Scenario
from .inventory_snapshot import (
    InventorySnapshot, SnapshotParseError, inventory_snapshot_from_space_values,
    validate_inventory_snapshot,
)


CONFIG_PATH_ENV = "MPH_SMS_LIVE_INVENTORY_CONFIG_PATH"
CONFIG_JSON_ENV = "MPH_SMS_LIVE_INVENTORY_CONFIG_JSON"
CONFIG_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_SITE_SKEW_SECONDS = 60.0
_ENV_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# =================================================================== errors
class LiveInventoryError(RuntimeError):
    code = "LIVE_INVENTORY_ERROR"


class LiveInventoryConfigError(LiveInventoryError):
    code = "CONFIG_ERROR"


class OptionalDependencyMissing(LiveInventoryError):
    code = "OPTIONAL_DEPENDENCY_MISSING"


class LiveInventoryConnectError(LiveInventoryError):
    code = "CONNECT_ERROR"


class LiveInventoryPublishError(LiveInventoryError):
    code = "PUBLISH_ERROR"


class LiveInventoryTimeout(LiveInventoryError):
    code = "RESPONSE_TIMEOUT"


class LiveInventoryProtocolError(LiveInventoryError):
    code = "PROTOCOL_ERROR"


class LiveInventorySiteError(LiveInventoryError):
    code = "SITE_ISOLATION_ERROR"


def _safe_exception_message(prefix: str, exc: BaseException) -> str:
    """Return an actionable message without echoing URLs or credentials."""
    return f"{prefix} ({type(exc).__name__})"


# =========================================================== wire/DTO types
@dataclass(frozen=True)
class OutboundMessage:
    correlation_id: str
    body: bytes
    message_type: str
    context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InboundMessage:
    correlation_id: str
    body: bytes
    content_type: str = "application/json"
    message_type: str = ""
    headers: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpaceValueResponse:
    site_id: str
    sms_id: str
    captured_at: datetime
    received_at: datetime
    correlation_ids: Tuple[str, ...]
    rows: Tuple[Mapping[str, Any], ...]
    protocol_version: str
    source: str
    payload_sha256: str
    connector_config_revision: str


@dataclass(frozen=True)
class LiveInventoryIssue:
    severity: str
    code: str
    message: str
    site: str = ""


@dataclass
class LiveInventoryPullResult:
    snapshot: Optional[InventorySnapshot]
    responses: Dict[str, SpaceValueResponse]
    issues: List[LiveInventoryIssue]
    requested_sites: List[str]
    scenario_fingerprint: str

    @property
    def ok(self) -> bool:
        return self.snapshot is not None and not any(
            i.severity == "ERROR" for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)


class RpcTransport(Protocol):
    """Transport seam used by the connector and fake unit tests."""

    def probe(self, config: "RabbitMqInventoryConfig", *,
              timeout_s: float) -> Mapping[str, Any]: ...

    def call_many(self, config: "RabbitMqInventoryConfig",
                  requests: Sequence[OutboundMessage], *,
                  timeout_s: float) -> Sequence[InboundMessage]: ...


# ============================================================= configuration
@dataclass(frozen=True)
class JsonSpaceValueProfile:
    request_mode: str
    request_template: Mapping[str, Any]
    response_items_path: str
    captured_at_path: str
    field_map: Mapping[str, str]
    quality_map: Mapping[str, str] = field(default_factory=dict)
    grade_map: Mapping[str, str] = field(default_factory=dict)
    records_are_segments: bool = False
    mass_scale_to_t: Optional[float] = None
    x_scale_to_m: Optional[float] = None
    age_scale_to_days: Optional[float] = None
    quality_scale: Mapping[str, float] = field(default_factory=dict)
    body_correlation_path: str = ""
    body_type_path: str = ""
    body_version_path: str = ""
    body_site_path: str = ""
    body_sms_path: str = ""
    expected_response_type: str = ""
    expected_protocol_version: str = ""
    expected_wire_site: str = ""
    expected_wire_sms: str = ""
    request_message_type: str = "SpaceValueRequest"
    pile_id_type: str = "string"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JsonSpaceValueProfile":
        return cls(
            request_mode=str(data.get("request_mode", "")).strip(),
            request_template=copy.deepcopy(data.get("request_template", {})),
            response_items_path=str(data.get("response_items_path", "")).strip(),
            captured_at_path=str(data.get("captured_at_path", "")).strip(),
            field_map={str(k): str(v) for k, v in
                       dict(data.get("field_map", {})).items()},
            quality_map={str(k): str(v) for k, v in
                         dict(data.get("quality_map", {})).items()},
            grade_map={str(k): str(v) for k, v in
                       dict(data.get("grade_map", {})).items()},
            records_are_segments=_strict_bool(
                data.get("records_are_segments", False),
                "space_value_profile.records_are_segments"),
            mass_scale_to_t=_optional_float(data.get("mass_scale_to_t")),
            x_scale_to_m=_optional_float(data.get("x_scale_to_m")),
            age_scale_to_days=_optional_float(data.get("age_scale_to_days")),
            quality_scale={str(k): float(v) for k, v in
                           dict(data.get("quality_scale", {})).items()},
            body_correlation_path=str(data.get("body_correlation_path", "")).strip(),
            body_type_path=str(data.get("body_type_path", "")).strip(),
            body_version_path=str(data.get("body_version_path", "")).strip(),
            body_site_path=str(data.get("body_site_path", "")).strip(),
            body_sms_path=str(data.get("body_sms_path", "")).strip(),
            expected_response_type=str(data.get("expected_response_type", "")).strip(),
            expected_protocol_version=str(
                data.get("expected_protocol_version", "")).strip(),
            expected_wire_site=str(data.get("expected_wire_site", "")).strip(),
            expected_wire_sms=str(data.get("expected_wire_sms", "")).strip(),
            request_message_type=str(
                data.get("request_message_type", "SpaceValueRequest")).strip(),
            pile_id_type=str(data.get("pile_id_type", "string")).strip(),
        )

    def validation_errors(self) -> List[str]:
        errors: List[str] = []
        if self.request_mode not in ("per_pile", "all"):
            errors.append("profile.request_mode must be 'per_pile' or 'all'")
        if not isinstance(self.request_template, Mapping) or not self.request_template:
            errors.append("profile.request_template is required from the ICD")
        if self.request_mode == "per_pile" and not _template_has_placeholder(
                self.request_template, "pile_id"):
            errors.append(
                "profile.request_template must contain {pile_id} in per_pile mode")
        if not self.captured_at_path:
            errors.append("profile.captured_at_path is required (server timestamp)")
        if "mass_t" not in self.field_map:
            errors.append("profile.field_map.mass_t is required")
        if self.request_mode == "all" and "pile" not in self.field_map:
            errors.append("profile.field_map.pile is required for all-pile responses")
        if self.records_are_segments:
            for key in ("x_from", "x_to"):
                if key not in self.field_map:
                    errors.append(f"profile.field_map.{key} is required for segments")
        if self.mass_scale_to_t is None or not math.isfinite(
                self.mass_scale_to_t) or self.mass_scale_to_t <= 0:
            errors.append("profile.mass_scale_to_t must explicitly define wire units")
        if self.records_are_segments and (
                self.x_scale_to_m is None or not math.isfinite(
                    self.x_scale_to_m) or self.x_scale_to_m <= 0):
            errors.append("profile.x_scale_to_m must explicitly define wire units")
        if "age_days" in self.field_map and (
                self.age_scale_to_days is None or not math.isfinite(
                    self.age_scale_to_days) or self.age_scale_to_days <= 0):
            errors.append("profile.age_scale_to_days must explicitly define wire units")
        if self.pile_id_type not in ("string", "int"):
            errors.append("profile.pile_id_type must be 'string' or 'int'")
        if not self.request_message_type:
            errors.append("profile.request_message_type is required from the ICD")
        if not self.expected_response_type:
            errors.append("profile.expected_response_type is required from the ICD")
        if not self.expected_protocol_version:
            errors.append(
                "profile.expected_protocol_version is required from the ICD")
        for key, value in self.quality_scale.items():
            if not math.isfinite(value) or value <= 0:
                errors.append(f"profile.quality_scale.{key} must be finite and > 0")
        if self.expected_protocol_version and not self.body_version_path:
            errors.append(
                "profile.body_version_path is required when a protocol version is expected")
        if self.expected_wire_site and not self.body_site_path:
            errors.append(
                "profile.body_site_path is required when a wire site is expected")
        if self.expected_wire_sms and not self.body_sms_path:
            errors.append(
                "profile.body_sms_path is required when a wire SMS is expected")
        if not ((self.expected_wire_site and self.body_site_path)
                or (self.expected_wire_sms and self.body_sms_path)):
            errors.append(
                "profile must verify a response site or SMS identity from the ICD")
        return errors


@dataclass(frozen=True)
class RabbitMqInventoryConfig:
    site_id: str
    sms_id: str
    enabled: bool
    amqp_url_env: str = field(repr=False)
    request_exchange: str
    request_routing_key: str
    profile: JsonSpaceValueProfile
    external_to_model: Mapping[str, str]
    timeout_s: float = 10.0
    connect_timeout_s: float = 3.0
    socket_timeout_s: float = 3.0
    heartbeat_s: int = 30
    tls_required: bool = True
    tls_ca_file_env: str = field(default="", repr=False)
    tls_cert_file_env: str = field(default="", repr=False)
    tls_key_file_env: str = field(default="", repr=False)
    tls_server_name: str = ""
    max_body_bytes: int = 5_000_000
    max_rows: int = 100_000
    max_response_skew_seconds: float = 60.0
    profile_revision: str = ""

    @classmethod
    def from_dict(cls, site_id: str,
                  data: Mapping[str, Any]) -> "RabbitMqInventoryConfig":
        connection = dict(data.get("connection", {}))
        limits = dict(data.get("limits", {}))
        tls = dict(connection.get("tls", {}))
        env_refs = {
            "connection.amqp_url_env": str(
                connection.get("amqp_url_env", "")).strip(),
            "connection.tls.ca_file_env": str(
                tls.get("ca_file_env", "")).strip(),
            "connection.tls.cert_file_env": str(
                tls.get("cert_file_env", "")).strip(),
            "connection.tls.key_file_env": str(
                tls.get("key_file_env", "")).strip(),
        }
        for label, ref in env_refs.items():
            if ref and not _valid_env_ref(ref):
                raise LiveInventoryConfigError(
                    f"{label} must contain an environment-variable name, not a value")
        return cls(
            site_id=site_id,
            sms_id=str(data.get("sms_id", f"{site_id}-SMS")).strip(),
            enabled=_strict_bool(data.get("enabled", False), "site.enabled"),
            amqp_url_env=env_refs["connection.amqp_url_env"],
            request_exchange=str(connection.get("request_exchange", "")).strip(),
            request_routing_key=str(
                connection.get("request_routing_key", "")).strip(),
            profile=JsonSpaceValueProfile.from_dict(
                dict(data.get("space_value_profile", {}))),
            external_to_model={str(k): str(v) for k, v in
                               dict(data.get("external_to_model", {})).items()},
            timeout_s=float(connection.get("timeout_s", 10.0)),
            connect_timeout_s=float(connection.get("connect_timeout_s", 3.0)),
            socket_timeout_s=float(connection.get("socket_timeout_s", 3.0)),
            heartbeat_s=int(connection.get("heartbeat_s", 30)),
            tls_required=_strict_bool(
                tls.get("required", True), "connection.tls.required"),
            tls_ca_file_env=env_refs["connection.tls.ca_file_env"],
            tls_cert_file_env=env_refs["connection.tls.cert_file_env"],
            tls_key_file_env=env_refs["connection.tls.key_file_env"],
            tls_server_name=str(tls.get("server_name", "")).strip(),
            max_body_bytes=int(limits.get("max_body_bytes", 5_000_000)),
            max_rows=int(limits.get("max_rows", 100_000)),
            max_response_skew_seconds=float(
                limits.get("max_response_skew_seconds", 60.0)),
            profile_revision=str(data.get("profile_revision", "")).strip(),
        )

    def safe_dict(self) -> dict:
        """Serializable configuration summary containing no secret value."""
        return {
            "site_id": self.site_id,
            "sms_id": self.sms_id,
            "enabled": self.enabled,
            "amqp_url_env": (_safe_env_ref(self.amqp_url_env)
                             if self.amqp_url_env else ""),
            "request_exchange_configured": bool(self.request_exchange),
            "request_routing_key_configured": bool(self.request_routing_key),
            "request_mode": self.profile.request_mode,
            "records_are_segments": self.profile.records_are_segments,
            "mapped_piles": len(self.external_to_model),
            "profile_revision": self.profile_revision,
            "tls_required": self.tls_required,
            "tls_ca_ref_configured": bool(self.tls_ca_file_env),
            "tls_client_cert_ref_configured": bool(self.tls_cert_file_env),
        }

    @property
    def revision_hash(self) -> str:
        # The config itself is secret-free by construction.  Include codec and
        # mapping content so a master-data change invalidates prior previews.
        payload = {
            "safe": self.safe_dict(),
            "profile": _jsonable(self.profile),
            "external_to_model": dict(self.external_to_model),
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def validation_errors(self, environ: Optional[Mapping[str, str]] = None,
                          *, require_secret: bool = True) -> List[str]:
        environ = environ if environ is not None else os.environ
        errors: List[str] = []
        if not self.enabled:
            errors.append("connector is disabled")
        if not self.site_id or not self.sms_id:
            errors.append("site_id and sms_id are required")
        if not self.amqp_url_env:
            errors.append("connection.amqp_url_env is required")
        elif not _valid_env_ref(self.amqp_url_env):
            errors.append("connection.amqp_url_env must be an environment-variable name")
        elif require_secret and not environ.get(self.amqp_url_env):
            errors.append(f"environment variable {self.amqp_url_env} is not set")
        if not self.request_routing_key:
            errors.append("connection.request_routing_key is required from the ICD")
        if not self.external_to_model:
            errors.append("external_to_model pile mapping is required")
        if len(set(self.external_to_model.values())) != len(self.external_to_model):
            errors.append("external_to_model targets must be one-to-one")
        if any(not math.isfinite(v) or v <= 0 for v in (
                self.timeout_s, self.connect_timeout_s, self.socket_timeout_s)):
            errors.append("connection timeouts must be finite and > 0")
        if not isinstance(self.heartbeat_s, int) or isinstance(
                self.heartbeat_s, bool) or self.heartbeat_s < 0:
            errors.append("connection.heartbeat_s must be >= 0")
        if (not isinstance(self.max_body_bytes, int)
                or isinstance(self.max_body_bytes, bool)
                or not isinstance(self.max_rows, int)
                or isinstance(self.max_rows, bool)
                or self.max_body_bytes <= 0 or self.max_rows <= 0):
            errors.append("response limits must be > 0")
        if not math.isfinite(self.max_response_skew_seconds) or \
                self.max_response_skew_seconds < 0:
            errors.append("max_response_skew_seconds must be finite and >= 0")
        tls_refs = {
            "connection.tls.ca_file_env": self.tls_ca_file_env,
            "connection.tls.cert_file_env": self.tls_cert_file_env,
            "connection.tls.key_file_env": self.tls_key_file_env,
        }
        for label, ref in tls_refs.items():
            if ref and not _valid_env_ref(ref):
                errors.append(f"{label} must be an environment-variable name")
            elif ref and require_secret and not environ.get(ref):
                errors.append(f"environment variable {ref} is not set")
        if bool(self.tls_cert_file_env) != bool(self.tls_key_file_env):
            errors.append(
                "both TLS client certificate and key references are required")
        errors.extend(self.profile.validation_errors())
        return errors


@dataclass
class LiveConfigBundle:
    configs: Dict[str, RabbitMqInventoryConfig]
    issues: List[str]
    revision_hash: str = ""
    source: str = "not-configured"


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise LiveInventoryConfigError(f"{label} must be a JSON boolean")
    return value


def _valid_env_ref(value: str) -> bool:
    return bool(_ENV_REF_RE.fullmatch(str(value or "")))


def _safe_env_ref(value: str) -> str:
    return value if _valid_env_ref(value) else "<invalid-env-ref>"


def _template_has_placeholder(value: Any, name: str) -> bool:
    if isinstance(value, Mapping):
        return any(_template_has_placeholder(v, name) for v in value.values())
    if isinstance(value, list):
        return any(_template_has_placeholder(v, name) for v in value)
    return isinstance(value, str) and ("{" + name + "}") in value


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {name: _jsonable(getattr(value, name))
                for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


_FORBIDDEN_SECRET_KEYS = {
    "password", "passwd", "token", "access_token", "api_key", "username",
    "credential", "credentials", "secret", "amqp_url", "uri", "url",
}


def _forbidden_secret_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    if normalized.endswith("env"):
        return False
    exact = {re.sub(r"[^a-z0-9]", "", key)
             for key in _FORBIDDEN_SECRET_KEYS} | {"user", "accesskey"}
    if normalized in exact:
        return True
    return (normalized.startswith(("password", "passwd", "secret", "credential",
                                   "accesstoken", "apikey", "accesskey"))
            or normalized.endswith(("password", "passwd", "secret", "credential",
                                    "token", "uri", "url")))

_ROOT_KEYS = {"schema_version", "__comment", "sites"}
_SITE_KEYS = {
    "enabled", "sms_id", "profile_revision", "connection", "limits",
    "external_to_model", "space_value_profile",
}
_CONNECTION_KEYS = {
    "amqp_url_env", "request_exchange", "request_routing_key", "timeout_s",
    "connect_timeout_s", "socket_timeout_s", "heartbeat_s", "tls",
}
_TLS_KEYS = {
    "required", "ca_file_env", "cert_file_env", "key_file_env", "server_name",
}
_LIMIT_KEYS = {
    "max_body_bytes", "max_rows", "max_response_skew_seconds",
}
_PROFILE_KEYS = {
    "request_mode", "request_template", "response_items_path",
    "captured_at_path", "field_map", "quality_map", "grade_map",
    "records_are_segments", "mass_scale_to_t", "x_scale_to_m",
    "age_scale_to_days", "quality_scale", "body_correlation_path",
    "body_type_path", "body_version_path", "body_site_path", "body_sms_path",
    "expected_response_type", "expected_protocol_version",
    "expected_wire_site", "expected_wire_sms", "request_message_type",
    "pile_id_type",
}


def _unknown_keys(value: Any, allowed: set[str], prefix: str) -> List[str]:
    if not isinstance(value, Mapping):
        return [f"{prefix} must be an object"]
    return [f"unknown connector config key: {prefix}.{key}"
            for key in value if str(key) not in allowed]


def _config_schema_errors(obj: Mapping[str, Any]) -> List[str]:
    """Reject ignored/typo fields so secret-looking values cannot hide there."""
    errors = _unknown_keys(obj, _ROOT_KEYS, "root")
    sites = obj.get("sites", {})
    if not isinstance(sites, Mapping):
        errors.append("sites must be an object")
        return errors
    for site, raw in sites.items():
        prefix = f"sites.{site}"
        errors.extend(_unknown_keys(raw, _SITE_KEYS, prefix))
        if not isinstance(raw, Mapping):
            continue
        connection = raw.get("connection", {})
        errors.extend(_unknown_keys(
            connection, _CONNECTION_KEYS, f"{prefix}.connection"))
        if isinstance(connection, Mapping):
            errors.extend(_unknown_keys(
                connection.get("tls", {}), _TLS_KEYS,
                f"{prefix}.connection.tls"))
        errors.extend(_unknown_keys(
            raw.get("limits", {}), _LIMIT_KEYS, f"{prefix}.limits"))
        profile = raw.get("space_value_profile", {})
        errors.extend(_unknown_keys(
            profile, _PROFILE_KEYS, f"{prefix}.space_value_profile"))
        for key in ("external_to_model",):
            if not isinstance(raw.get(key, {}), Mapping):
                errors.append(f"{prefix}.{key} must be an object")
        if isinstance(profile, Mapping):
            for key in ("request_template", "field_map", "quality_map",
                        "grade_map", "quality_scale"):
                if not isinstance(profile.get(key, {}), Mapping):
                    errors.append(
                        f"{prefix}.space_value_profile.{key} must be an object")
    return errors


def _secret_key_paths(value: Any, prefix: str = "") -> List[str]:
    found: List[str] = []
    if isinstance(value, Mapping):
        if any(prefix.endswith(name) for name in (
                "external_to_model", "field_map", "quality_map", "grade_map",
                "quality_scale")):
            return found
        for key, item in value.items():
            key_s = str(key)
            path = f"{prefix}.{key_s}" if prefix else key_s
            if _forbidden_secret_key(key_s):
                found.append(path)
            found.extend(_secret_key_paths(item, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(_secret_key_paths(item, f"{prefix}[{idx}]"))
    return found


def load_live_inventory_configs(
        environ: Optional[Mapping[str, str]] = None,
        *, read_text: Optional[Callable[[str], str]] = None,
) -> LiveConfigBundle:
    """Load a secret-free connector bundle from an env JSON value or path."""
    environ = environ if environ is not None else os.environ
    raw_json = environ.get(CONFIG_JSON_ENV, "")
    source = "not-configured"
    if raw_json:
        text = raw_json
        source = CONFIG_JSON_ENV
    else:
        path = environ.get(CONFIG_PATH_ENV, "")
        if not path:
            return LiveConfigBundle({}, [], source=source)
        source = CONFIG_PATH_ENV
        try:
            text = ((read_text or (lambda p: Path(p).read_text(encoding="utf-8")))(
                path))
        except Exception as exc:
            return LiveConfigBundle(
                {}, [_safe_exception_message("cannot read live inventory config", exc)],
                source=source)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return LiveConfigBundle({}, [f"live inventory config is invalid JSON "
                                     f"(line {exc.lineno})"], source=source)
    if not isinstance(obj, Mapping):
        return LiveConfigBundle({}, ["live inventory config root must be an object"],
                                source=source)
    schema_errors = _config_schema_errors(obj)
    if schema_errors:
        return LiveConfigBundle({}, schema_errors, source=source)
    secret_paths = _secret_key_paths(obj)
    if secret_paths:
        return LiveConfigBundle(
            {}, ["secret-bearing keys are forbidden in connector config: "
                 + ", ".join(secret_paths)], source=source)
    schema = str(obj.get("schema_version", ""))
    if schema != CONFIG_SCHEMA_VERSION:
        return LiveConfigBundle(
            {}, [f"unsupported live inventory config schema_version '{schema}'"],
            source=source)
    sites = obj.get("sites", {})
    if not isinstance(sites, Mapping):
        return LiveConfigBundle({}, ["live inventory config sites must be an object"],
                                source=source)
    configs: Dict[str, RabbitMqInventoryConfig] = {}
    issues: List[str] = []
    for site_id, site_data in sites.items():
        if not isinstance(site_data, Mapping):
            issues.append(f"site {site_id} configuration must be an object")
            continue
        try:
            configs[str(site_id)] = RabbitMqInventoryConfig.from_dict(
                str(site_id), site_data)
        except (LiveInventoryConfigError, TypeError, ValueError) as exc:
            issues.append(_safe_exception_message(
                f"site {site_id} configuration is invalid", exc))
    revision = hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return LiveConfigBundle(configs, issues, revision_hash=revision, source=source)


# ================================================================ JSON codec
def _path_get(value: Any, path: str, *, required: bool = False,
              label: str = "field") -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit() and \
                int(part) < len(current):
            current = current[int(part)]
        else:
            if required:
                raise LiveInventoryProtocolError(
                    f"required {label} path '{path}' is missing")
            return None
    return current


def _parse_aware_datetime(value: Any, label: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LiveInventoryProtocolError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LiveInventoryProtocolError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveInventoryProtocolError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise LiveInventoryProtocolError(f"{label} is not finite")
    return number


def _scaled_number(value: Any, scale: float, label: str) -> float:
    number = _finite_number(value, label) * scale
    if not math.isfinite(number):
        raise LiveInventoryProtocolError(
            f"{label} overflows after configured unit scaling")
    return number


def _wire_pile_id(value: str, kind: str) -> Any:
    if kind == "int":
        try:
            return int(value)
        except ValueError as exc:
            raise LiveInventoryConfigError(
                f"external pile ID '{value}' cannot be encoded as int") from exc
    return value


def _render_template(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_template(v, context) for v in value]
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}") and \
                value.count("{") == 1 and value.count("}") == 1:
            key = value[1:-1]
            if key in context:
                return context[key]
        try:
            return value.format_map(dict(context))
        except KeyError as exc:
            raise LiveInventoryConfigError(
                f"request template uses unknown placeholder {exc}") from exc
    return value


class JsonSpaceValueCodec:
    """Strict JSON codec whose paths/template come entirely from the ICD profile."""

    def encode_requests(
            self, config: RabbitMqInventoryConfig,
            *, id_factory: Callable[[], str],
    ) -> List[OutboundMessage]:
        profile = config.profile
        if profile.request_mode == "per_pile":
            external_ids: Iterable[Optional[str]] = sorted(
                config.external_to_model.keys())
        else:
            external_ids = [None]
        out: List[OutboundMessage] = []
        pile_ids = [_wire_pile_id(p, profile.pile_id_type)
                    for p in sorted(config.external_to_model)]
        for external in external_ids:
            correlation_id = id_factory()
            context = {
                "site_id": config.site_id,
                "sms_id": config.sms_id,
                "request_id": correlation_id,
                "pile_id": (_wire_pile_id(external, profile.pile_id_type)
                            if external is not None else ""),
                "pile_ids": pile_ids,
            }
            rendered = _render_template(profile.request_template, context)
            body = json.dumps(rendered, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8")
            out.append(OutboundMessage(
                correlation_id=correlation_id, body=body,
                message_type=profile.request_message_type,
                context={"external_pile": external or ""},
            ))
        return out

    def decode_response(
            self, config: RabbitMqInventoryConfig,
            request: OutboundMessage,
            message: InboundMessage,
            *, received_at: datetime,
    ) -> Tuple[datetime, List[dict], str]:
        profile = config.profile
        if message.correlation_id != request.correlation_id:
            raise LiveInventoryProtocolError("AMQP correlation_id mismatch")
        if not message.content_type.lower().startswith("application/json"):
            raise LiveInventoryProtocolError(
                f"unexpected content type '{message.content_type}'")
        if profile.expected_response_type and \
                message.message_type != profile.expected_response_type:
            raise LiveInventoryProtocolError("unexpected AMQP response type")
        if len(message.body) > config.max_body_bytes:
            raise LiveInventoryProtocolError(
                f"response exceeds {config.max_body_bytes} byte limit")
        try:
            payload = json.loads(message.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveInventoryProtocolError("response body is not valid UTF-8 JSON") \
                from exc
        if not isinstance(payload, Mapping):
            raise LiveInventoryProtocolError("response JSON root must be an object")

        checks = [
            (profile.body_correlation_path, request.correlation_id,
             "body correlation ID"),
            (profile.body_type_path, profile.expected_response_type,
             "response type"),
            (profile.body_version_path, profile.expected_protocol_version,
             "protocol version"),
            (profile.body_site_path, profile.expected_wire_site,
             "wire site"),
            (profile.body_sms_path, profile.expected_wire_sms,
             "wire SMS"),
        ]
        for path, expected, label in checks:
            if path:
                actual = _path_get(payload, path, required=True, label=label)
                if expected and str(actual) != expected:
                    raise LiveInventoryProtocolError(f"{label} mismatch")
            elif expected and label in ("protocol version", "wire site", "wire SMS"):
                raise LiveInventoryConfigError(
                    f"{label} is configured but its body path is missing")

        captured_raw = _path_get(
            payload, profile.captured_at_path, required=True,
            label="server captured_at")
        captured_at = _parse_aware_datetime(captured_raw, "server captured_at")
        items = _path_get(payload, profile.response_items_path,
                          required=True, label="response items")
        if isinstance(items, Mapping):
            items = [items]
        if not isinstance(items, list):
            raise LiveInventoryProtocolError("response items must be a list/object")
        if len(items) > config.max_rows:
            raise LiveInventoryProtocolError(
                f"response contains {len(items)} rows; limit is {config.max_rows}")
        external_expected = request.context.get("external_pile", "")
        rows: List[dict] = []
        for idx, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise LiveInventoryProtocolError(
                    f"response item {idx} must be an object")
            pile_raw = (_path_get(item, profile.field_map.get("pile", ""),
                                  required=not bool(external_expected),
                                  label=f"item {idx} pile")
                        if profile.field_map.get("pile") else external_expected)
            if pile_raw in (None, ""):
                pile_raw = external_expected
            external_pile = str(pile_raw)
            if external_expected and external_pile != external_expected:
                raise LiveInventorySiteError(
                    f"response pile '{external_pile}' does not match requested pile")
            if external_pile not in config.external_to_model:
                raise LiveInventorySiteError(
                    f"response pile '{external_pile}' has no explicit model mapping")

            mass_raw = _path_get(
                item, profile.field_map["mass_t"], required=True,
                label=f"item {idx} mass")
            row: Dict[str, Any] = {
                "site": config.site_id,
                "pile": config.external_to_model[external_pile],
                "record_type": ("segment" if profile.records_are_segments
                                else "pile"),
                "mass_t": _scaled_number(
                    mass_raw, float(profile.mass_scale_to_t),
                    f"item {idx} mass"),
                "captured_at": captured_at.isoformat(),
            }
            if "grade" in profile.field_map:
                grade_raw = _path_get(item, profile.field_map["grade"],
                                      label=f"item {idx} grade")
                if grade_raw not in (None, ""):
                    grade_key = str(grade_raw)
                    if profile.grade_map:
                        if grade_key not in profile.grade_map:
                            raise LiveInventoryProtocolError(
                                f"item {idx} grade has no explicit mapping")
                        row["grade"] = profile.grade_map[grade_key]
                    else:
                        row["grade"] = grade_key
            quality: Dict[str, float] = {}
            if "quality" in profile.field_map:
                qraw = _path_get(item, profile.field_map["quality"],
                                 label=f"item {idx} quality")
                if qraw is not None:
                    if not isinstance(qraw, Mapping):
                        raise LiveInventoryProtocolError(
                            f"item {idx} quality must be an object")
                    for key, value in qraw.items():
                        quality[str(key)] = _finite_number(
                            value, f"item {idx} quality.{key}")
            for canonical, path in profile.quality_map.items():
                value = _path_get(item, path, label=f"item {idx} {canonical}")
                if value not in (None, ""):
                    quality[canonical] = _finite_number(
                        value, f"item {idx} quality.{canonical}")
            for key in list(quality):
                quality[key] = _scaled_number(
                    quality[key], float(profile.quality_scale.get(key, 1.0)),
                    f"item {idx} quality.{key}")
            if quality:
                row["quality"] = quality
            if profile.records_are_segments:
                row["x_from"] = _scaled_number(
                    _path_get(item, profile.field_map["x_from"], required=True,
                              label=f"item {idx} x_from"),
                    float(profile.x_scale_to_m), f"item {idx} x_from")
                row["x_to"] = _scaled_number(
                    _path_get(item, profile.field_map["x_to"], required=True,
                              label=f"item {idx} x_to"),
                    float(profile.x_scale_to_m), f"item {idx} x_to")
            if "age_days" in profile.field_map:
                age_raw = _path_get(item, profile.field_map["age_days"],
                                    label=f"item {idx} age")
                if age_raw not in (None, ""):
                    row["age_days"] = _scaled_number(
                        age_raw, float(profile.age_scale_to_days),
                        f"item {idx} age")
            if "state" in profile.field_map:
                state = _path_get(item, profile.field_map["state"],
                                  label=f"item {idx} state")
                if state not in (None, ""):
                    row["state"] = str(state)
            rows.append(row)
        digest = hashlib.sha256(message.body).hexdigest()
        return captured_at, rows, digest


# =============================================================== Pika RPC I/O
class PikaRpcTransport:
    """Request-scoped Pika RPC transport with an exclusive callback queue."""

    def __init__(self, url_provider: Optional[Callable[[str], str]] = None,
                 monotonic: Callable[[], float] = time.monotonic):
        self._url_provider = url_provider or (lambda name: os.environ.get(name, ""))
        self._monotonic = monotonic

    @staticmethod
    def _pika():
        try:
            import pika  # type: ignore
        except ImportError as exc:
            raise OptionalDependencyMissing(
                "RabbitMQ client is not installed; install "
                "requirements-rabbitmq.txt") from exc
        return pika

    def _parameters(self, config: RabbitMqInventoryConfig, timeout_s: float):
        pika = self._pika()
        if not _valid_env_ref(config.amqp_url_env):
            raise LiveInventoryConfigError(
                "AMQP secret reference must be an environment-variable name")
        url = self._url_provider(config.amqp_url_env)
        if not url:
            raise LiveInventoryConfigError(
                f"environment variable {config.amqp_url_env} is not set")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise LiveInventoryConfigError("AMQP URL secret is malformed") from exc
        if parsed.scheme not in ("amqp", "amqps"):
            raise LiveInventoryConfigError("AMQP URL must use amqp:// or amqps://")
        if config.tls_required and parsed.scheme != "amqps":
            raise LiveInventoryConfigError("TLS is required but AMQP URL is not amqps://")
        try:
            params = pika.URLParameters(url)
            params.connection_attempts = 1
            params.retry_delay = 0
            params.socket_timeout = min(config.socket_timeout_s, timeout_s)
            params.blocked_connection_timeout = min(
                config.connect_timeout_s, timeout_s)
            params.heartbeat = config.heartbeat_s
            if hasattr(params, "stack_timeout"):
                params.stack_timeout = min(config.connect_timeout_s, timeout_s)
            if parsed.scheme == "amqps":
                ca_file = (self._url_provider(config.tls_ca_file_env)
                           if config.tls_ca_file_env else None)
                if config.tls_ca_file_env and not ca_file:
                    raise LiveInventoryConfigError(
                        "configured TLS CA environment variable is not set")
                context = ssl.create_default_context(cafile=ca_file or None)
                cert_file = (self._url_provider(config.tls_cert_file_env)
                             if config.tls_cert_file_env else "")
                key_file = (self._url_provider(config.tls_key_file_env)
                            if config.tls_key_file_env else "")
                if bool(config.tls_cert_file_env) != bool(config.tls_key_file_env):
                    raise LiveInventoryConfigError(
                        "both TLS client certificate and key references are required")
                if config.tls_cert_file_env and (not cert_file or not key_file):
                    raise LiveInventoryConfigError(
                        "configured TLS client certificate/key environment "
                        "variables are not both set")
                if cert_file:
                    context.load_cert_chain(cert_file, key_file)
                server_name = config.tls_server_name or parsed.hostname or ""
                params.ssl_options = pika.SSLOptions(context, server_name)
            return params
        except LiveInventoryError:
            raise
        except Exception as exc:
            raise LiveInventoryConfigError(
                _safe_exception_message("cannot build RabbitMQ parameters", exc)) from exc

    def probe(self, config: RabbitMqInventoryConfig, *,
              timeout_s: float) -> Mapping[str, Any]:
        pika = self._pika()
        started = self._monotonic()
        connection = None
        try:
            connection = pika.BlockingConnection(self._parameters(config, timeout_s))
            return {"reachable": bool(connection.is_open),
                    "latency_ms": round((self._monotonic() - started) * 1000)}
        except LiveInventoryError:
            raise
        except Exception as exc:
            raise LiveInventoryConnectError(
                _safe_exception_message("RabbitMQ probe failed", exc)) from exc
        finally:
            if connection is not None and getattr(connection, "is_open", False):
                try:
                    connection.close()
                except Exception:
                    pass

    def call_many(self, config: RabbitMqInventoryConfig,
                  requests: Sequence[OutboundMessage], *,
                  timeout_s: float) -> Sequence[InboundMessage]:
        if not requests:
            return []
        pika = self._pika()
        deadline = self._monotonic() + timeout_s
        connection = None
        channel = None
        responses: Dict[str, InboundMessage] = {}
        protocol_errors: List[str] = []
        expected = {r.correlation_id for r in requests}
        try:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise LiveInventoryTimeout("RabbitMQ request deadline expired")
            connection = pika.BlockingConnection(self._parameters(config, remaining))
            channel = connection.channel()
            channel.confirm_delivery()
            callback_queue = channel.queue_declare(
                queue="", exclusive=True, auto_delete=True).method.queue

            def on_response(ch, method, props, body):
                correlation = str(getattr(props, "correlation_id", "") or "")
                if correlation not in expected:
                    protocol_errors.append("unexpected response correlation_id")
                    ch.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
                    return
                if correlation in responses:
                    protocol_errors.append("duplicate response correlation_id")
                    ch.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
                    return
                responses[correlation] = InboundMessage(
                    correlation_id=correlation,
                    body=bytes(body),
                    content_type=str(getattr(props, "content_type", "") or ""),
                    message_type=str(getattr(props, "type", "") or ""),
                    headers=dict(getattr(props, "headers", {}) or {}),
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue=callback_queue,
                                  on_message_callback=on_response,
                                  auto_ack=False)
            for request in requests:
                remaining_ms = max(int((deadline - self._monotonic()) * 1000), 1)
                properties = pika.BasicProperties(
                    content_type="application/json",
                    content_encoding="utf-8",
                    correlation_id=request.correlation_id,
                    reply_to=callback_queue,
                    type=request.message_type,
                    expiration=str(remaining_ms),
                    delivery_mode=1,
                )
                try:
                    published = channel.basic_publish(
                        exchange=config.request_exchange,
                        routing_key=config.request_routing_key,
                        body=request.body,
                        properties=properties,
                        mandatory=True,
                    )
                except Exception as exc:
                    raise LiveInventoryPublishError(
                        _safe_exception_message("RabbitMQ publish failed", exc)) from exc
                if published is False:
                    raise LiveInventoryPublishError(
                        "RabbitMQ publisher confirm was negative")

            while len(responses) < len(requests):
                if protocol_errors:
                    raise LiveInventoryProtocolError(protocol_errors[0])
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise LiveInventoryTimeout(
                        f"timed out waiting for {len(requests) - len(responses)} "
                        "SpaceValueResponse telegram(s)")
                connection.process_data_events(time_limit=min(0.2, remaining))
            return [responses[r.correlation_id] for r in requests]
        except LiveInventoryError:
            raise
        except Exception as exc:
            raise LiveInventoryConnectError(
                _safe_exception_message("RabbitMQ request failed", exc)) from exc
        finally:
            if channel is not None and getattr(channel, "is_open", False):
                try:
                    channel.close()
                except Exception:
                    pass
            if connection is not None and getattr(connection, "is_open", False):
                try:
                    connection.close()
                except Exception:
                    pass


# ============================================================= connector/API
class RabbitMqInventoryConnector:
    """One immutable, read-only connector for one SMS/site."""

    def __init__(
            self,
            config: RabbitMqInventoryConfig,
            transport: Optional[RpcTransport] = None,
            codec: Optional[JsonSpaceValueCodec] = None,
            *,
            environ: Optional[Mapping[str, str]] = None,
            now: Optional[Callable[[], datetime]] = None,
            id_factory: Optional[Callable[[], str]] = None,
    ):
        self.config = config
        self.environ = environ if environ is not None else os.environ
        self.transport = transport or PikaRpcTransport(
            url_provider=lambda name: self.environ.get(name, ""))
        self.codec = codec or JsonSpaceValueCodec()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def readiness(self) -> dict:
        errors = self.config.validation_errors(self.environ)
        return {
            "site": self.config.site_id,
            "sms_id": self.config.sms_id,
            "mode": "configured-unchecked" if not errors else "not-configured",
            "ready": not errors,
            "missing_or_invalid": errors,
            "config_revision": self.config.revision_hash,
            **self.config.safe_dict(),
        }

    def health(self, timeout_s: float = 2.0) -> dict:
        ready = self.readiness()
        if not ready["ready"]:
            return ready
        try:
            result = dict(self.transport.probe(self.config, timeout_s=timeout_s))
            return {
                **ready, "mode": "reachable", "reachable": True,
                "checked_at": self._normalised_now().isoformat(),
                "detail": "read-only inventory endpoint reachable",
                **{k: v for k, v in result.items()
                   if isinstance(v, (str, int, float, bool))},
            }
        except LiveInventoryError as exc:
            return {
                **ready, "mode": "error", "reachable": False,
                "checked_at": self._normalised_now().isoformat(),
                "error_code": exc.code, "detail": str(exc),
            }

    def fetch(self, timeout_s: Optional[float] = None) -> SpaceValueResponse:
        errors = self.config.validation_errors(self.environ)
        if errors:
            raise LiveInventoryConfigError("; ".join(errors))
        requests = self.codec.encode_requests(
            self.config, id_factory=self._id_factory)
        responses = self.transport.call_many(
            self.config, requests,
            timeout_s=float(timeout_s or self.config.timeout_s))
        if len(responses) != len(requests):
            raise LiveInventoryProtocolError(
                "transport returned a different response count")
        decoded_rows: List[dict] = []
        captured_times: List[datetime] = []
        payload_hashes: List[str] = []
        received_at = self._normalised_now()
        for request, response in zip(requests, responses):
            captured, rows, digest = self.codec.decode_response(
                self.config, request, response, received_at=received_at)
            captured_times.append(captured)
            decoded_rows.extend(rows)
            payload_hashes.append(digest)
        if not decoded_rows:
            raise LiveInventoryProtocolError("SpaceValueResponse contains no rows")
        skew = ((max(captured_times) - min(captured_times)).total_seconds()
                if captured_times else 0.0)
        if skew > self.config.max_response_skew_seconds:
            raise LiveInventoryProtocolError(
                f"per-pile response timestamp skew {skew:.1f}s exceeds "
                f"{self.config.max_response_skew_seconds:g}s")
        combined_hash = hashlib.sha256(
            "".join(payload_hashes).encode()).hexdigest()
        return SpaceValueResponse(
            site_id=self.config.site_id,
            sms_id=self.config.sms_id,
            captured_at=max(captured_times),
            received_at=received_at,
            correlation_ids=tuple(r.correlation_id for r in requests),
            rows=tuple(decoded_rows),
            protocol_version=self.config.profile.expected_protocol_version,
            source=f"RabbitMQ SpaceValueResponse · {self.config.sms_id}",
            payload_sha256=combined_hash,
            connector_config_revision=self.config.revision_hash,
        )

    def _normalised_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LiveInventoryConfigError("connector clock must be timezone-aware")
        return value.astimezone(timezone.utc)


def scenario_inventory_fingerprint(scenario: Scenario) -> str:
    """Hash master data that can change live-snapshot interpretation."""
    payload = {
        "spaces": sorted((
            sp.site, sp.id, sp.capacity_t, sp.x_from, sp.x_to,
            sp.material_class, sp.profile_id,
        ) for sp in scenario.spaces),
        "grades": sorted((
            grade.id, grade.material_class, grade.profile_id,
            sorted(grade.quality.items()),
        ) for grade in scenario.grades),
        "quality_params": sorted((q.name, q.unit) for q in
                                 scenario.quality_params),
        "zones_enabled": bool(
            scenario.config.get("MULTI_GRADE_ZONES_ENABLED")),
        "zones": sorted((
            z.space_id, z.zone_id, z.x_from, z.x_to, z.grade,
        ) for z in scenario.pile_zones),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def pull_live_inventory_snapshot(
        scenario: Scenario,
        connectors: Mapping[str, RabbitMqInventoryConnector],
        *,
        sites: Optional[Sequence[str]] = None,
        max_site_skew_seconds: float = DEFAULT_MAX_SITE_SKEW_SECONDS,
        max_age_hours: float = 0.5,
        now: Optional[datetime] = None,
        require_complete: bool = True,
) -> LiveInventoryPullResult:
    """Fetch all sites first, then build one validated snapshot atomically."""
    expected_sites = sorted({sp.site for sp in scenario.spaces})
    requested_sites = sorted(set(sites or expected_sites))
    issues: List[LiveInventoryIssue] = []
    responses: Dict[str, SpaceValueResponse] = {}
    fingerprint = scenario_inventory_fingerprint(scenario)
    if not math.isfinite(max_site_skew_seconds) or max_site_skew_seconds < 0:
        issues.append(LiveInventoryIssue(
            "ERROR", "BAD_SITE_SKEW_LIMIT",
            "max_site_skew_seconds must be finite and non-negative"))
    if not math.isfinite(max_age_hours) or max_age_hours < 0:
        issues.append(LiveInventoryIssue(
            "ERROR", "BAD_FRESHNESS_LIMIT",
            "max_age_hours must be finite and non-negative"))
    if issues:
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)
    for site in requested_sites:
        connector = connectors.get(site)
        if connector is None:
            issues.append(LiveInventoryIssue(
                "ERROR", "CONNECTOR_MISSING",
                "no configured read-only inventory connector for loaded site", site))
            continue
        if connector.config.site_id != site:
            issues.append(LiveInventoryIssue(
                "ERROR", "SITE_ISOLATION_ERROR",
                "connector site does not match requested site", site))
            continue
        try:
            response = connector.fetch()
            if response.site_id != site:
                raise LiveInventorySiteError("response site does not match connector")
            responses[site] = response
        except LiveInventoryError as exc:
            issues.append(LiveInventoryIssue(
                "ERROR", exc.code, str(exc), site))
    if any(i.severity == "ERROR" for i in issues):
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)
    if require_complete and set(requested_sites) != set(expected_sites):
        issues.append(LiveInventoryIssue(
            "ERROR", "INCOMPLETE_SITE_SET",
            "live planning snapshot must include every loaded SMS site"))
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)

    capture_times = [r.captured_at for r in responses.values()]
    if not capture_times:
        issues.append(LiveInventoryIssue(
            "ERROR", "EMPTY_LIVE_PULL", "no SpaceValue responses were returned"))
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)
    site_skew = (max(capture_times) - min(capture_times)).total_seconds()
    if site_skew > max_site_skew_seconds:
        issues.append(LiveInventoryIssue(
            "ERROR", "SITE_TIMESTAMP_SKEW",
            f"site capture-time skew {site_skew:.1f}s exceeds "
            f"{max_site_skew_seconds:g}s"))
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)

    rows: List[dict] = []
    known_spaces = {sp.id: sp for sp in scenario.spaces}
    for site, response in responses.items():
        for raw in response.rows:
            row = dict(raw)
            pile_id = str(row.get("pile") or row.get("pile_id") or
                          row.get("space_id") or "")
            sp = known_spaces.get(pile_id)
            if sp is None:
                issues.append(LiveInventoryIssue(
                    "ERROR", "UNKNOWN_MODEL_PILE",
                    f"decoded pile '{pile_id}' is not loaded", site))
                continue
            if sp.site != site:
                issues.append(LiveInventoryIssue(
                    "ERROR", "CROSS_SITE_MAPPING",
                    f"decoded pile '{pile_id}' belongs to {sp.site}, not {site}",
                    site))
                continue
            row["site"] = site
            rows.append(row)
    if any(i.severity == "ERROR" for i in issues):
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)

    global_t0 = max(capture_times)
    site_times = {site: response.captured_at.isoformat()
                  for site, response in responses.items()}
    connector_revision = hashlib.sha256("|".join(
        sorted(r.connector_config_revision for r in responses.values()))
        .encode()).hexdigest()[:16]
    source = "RabbitMQ SpaceValueResponse · " + ", ".join(
        responses[s].sms_id for s in sorted(responses))
    try:
        snapshot = inventory_snapshot_from_space_values(
            rows, captured_at=global_t0, source=source,
            acquisition_mode="live-read-only",
            site_captured_at=site_times,
            connector_config_revision=connector_revision)
    except SnapshotParseError as exc:
        issues.append(LiveInventoryIssue(
            "ERROR", LiveInventoryProtocolError.code,
            f"decoded SpaceValue rows violate the inventory contract: {exc}"))
        return LiveInventoryPullResult(None, responses, issues,
                                       requested_sites, fingerprint)
    snapshot.payload_sha256 = hashlib.sha256("|".join(
        sorted(r.payload_sha256 for r in responses.values())).encode()
    ).hexdigest()
    validation_now = now or datetime.now(timezone.utc)
    validation = validate_inventory_snapshot(
        scenario, snapshot, max_age_hours=max_age_hours,
        now=validation_now, require_complete=require_complete)
    for item in validation.issues:
        issues.append(LiveInventoryIssue(
            item.severity, item.code, item.message, item.entity))
    if validation.has_errors:
        snapshot = None
    return LiveInventoryPullResult(snapshot, responses, issues,
                                   requested_sites, fingerprint)


def live_inventory_config_template() -> bytes:
    """Secret-free skeleton. Every ICD-dependent field is deliberately blank."""
    site_template = {
        "enabled": False,
        "sms_id": "<SITE>-SMS",
        "profile_revision": "TBC-from-vendor-ICD",
        "connection": {
            "amqp_url_env": "<SITE>_SMS_AMQP_URL",
            "request_exchange": "",
            "request_routing_key": "",
            "timeout_s": 10,
            "connect_timeout_s": 3,
            "socket_timeout_s": 3,
            "heartbeat_s": 30,
            "tls": {"required": True, "ca_file_env": "",
                    "cert_file_env": "", "key_file_env": "",
                    "server_name": ""},
        },
        "limits": {"max_body_bytes": 5_000_000, "max_rows": 100_000,
                   "max_response_skew_seconds": 60},
        "external_to_model": {},
        "space_value_profile": {
            "request_mode": "per_pile",
            "request_message_type": "SpaceValueRequest",
            "request_template": {},
            "response_items_path": "",
            "captured_at_path": "",
            "field_map": {},
            "quality_map": {},
            "grade_map": {},
            "records_are_segments": True,
            "mass_scale_to_t": None,
            "x_scale_to_m": None,
            "age_scale_to_days": None,
            "quality_scale": {},
            "body_correlation_path": "",
            "body_type_path": "",
            "body_version_path": "",
            "body_site_path": "",
            "body_sms_path": "",
            "expected_response_type": "",
            "expected_protocol_version": "",
            "expected_wire_site": "",
            "expected_wire_sms": "",
            "pile_id_type": "string",
        },
    }
    obj = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "__comment": ("Fill only from the approved RabbitMQ ICD/config export. "
                       "Do not put credentials or AMQP URLs in this file; set "
                       "the referenced environment variables instead."),
        "sites": {site: {**copy.deepcopy(site_template),
                          "sms_id": f"{site}-SMS",
                          "connection": {
                              **copy.deepcopy(site_template["connection"]),
                              "amqp_url_env": f"{site}_SMS_AMQP_URL",
                          }}
                  for site in ("LOUTA", "BENGUERIR", "MPH")},
    }
    return json.dumps(obj, indent=2).encode("utf-8")
