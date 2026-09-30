"""Contract tests for the read-only RabbitMQ SpaceValue boundary.

All network behaviour is represented by :class:`FakeRpcTransport`.  These
tests exercise the configured codec, site isolation, secret handling and the
all-sites-before-commit rule without importing Pika or opening a socket.
"""
from __future__ import annotations

import copy
import itertools
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Optional, Sequence

import pytest

from mph_sim.domain import Grade, QualityParam, Scenario, Site, StockSpace
from mph_sim.rabbitmq_inventory import (
    CONFIG_JSON_ENV,
    InboundMessage,
    JsonSpaceValueProfile,
    LiveInventoryConfigError,
    LiveInventoryProtocolError,
    LiveInventorySiteError,
    LiveInventoryTimeout,
    OptionalDependencyMissing,
    OutboundMessage,
    RabbitMqInventoryConfig,
    RabbitMqInventoryConnector,
    RpcTransport,
    load_live_inventory_configs,
    pull_live_inventory_snapshot,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
CAPTURED = NOW - timedelta(minutes=10)
AMQP_ENV = "TEST_SMS_AMQP_URL"
AMQP_VALUE = "amqps://test-user:test-password@broker.invalid/vhost"
SENTINEL_SECRET = "SENTINEL-do-not-leak-4e9c7d"


class FakeRpcTransport(RpcTransport):
    """Deterministic RPC seam; never imports Pika or opens a socket."""

    def __init__(
            self,
            response_factory: Optional[
                Callable[[RabbitMqInventoryConfig, OutboundMessage],
                         InboundMessage]
            ] = None,
            *,
            call_error: Optional[BaseException] = None,
            probe_error: Optional[BaseException] = None,
    ):
        self.response_factory = response_factory
        self.call_error = call_error
        self.probe_error = probe_error
        self.calls: list[tuple[str, list[OutboundMessage], float]] = []
        self.probes: list[tuple[str, float]] = []

    def probe(self, config: RabbitMqInventoryConfig, *,
              timeout_s: float) -> Mapping[str, object]:
        self.probes.append((config.site_id, timeout_s))
        if self.probe_error is not None:
            raise self.probe_error
        return {"reachable": True, "latency_ms": 1}

    def call_many(self, config: RabbitMqInventoryConfig,
                  requests: Sequence[OutboundMessage], *,
                  timeout_s: float) -> Sequence[InboundMessage]:
        self.calls.append((config.site_id, list(requests), timeout_s))
        if self.call_error is not None:
            raise self.call_error
        if self.response_factory is None:
            raise AssertionError("fake response_factory was not configured")
        return [self.response_factory(config, request)
                for request in requests]


def _profile(site: str, sms_id: str) -> JsonSpaceValueProfile:
    return JsonSpaceValueProfile(
        request_mode="per_pile",
        request_template={
            "requestId": "{request_id}",
            "site": "{site_id}",
            "sms": "{sms_id}",
            "pileId": "{pile_id}",
        },
        response_items_path="segments",
        captured_at_path="meta.capturedAt",
        field_map={
            "pile": "pileId",
            "mass_t": "massKg",
            "grade": "gradeCode",
            "x_from": "xFromCm",
            "x_to": "xToCm",
            "age_days": "ageHours",
        },
        quality_map={"BPL": "quality.bpl"},
        grade_map={"WIRE-G": "G"},
        records_are_segments=True,
        mass_scale_to_t=0.001,
        x_scale_to_m=0.01,
        age_scale_to_days=1.0 / 24.0,
        quality_scale={"BPL": 1.0},
        body_correlation_path="meta.correlationId",
        body_type_path="meta.type",
        body_version_path="meta.version",
        body_site_path="meta.site",
        body_sms_path="meta.sms",
        expected_response_type="SpaceValueResponse",
        expected_protocol_version="1.0",
        expected_wire_site=site,
        expected_wire_sms=sms_id,
        request_message_type="SpaceValueRequest",
        pile_id_type="string",
    )


def _config(site: str = "MPH", model_pile: str = "HP-01", *,
            external_pile: Optional[str] = None) -> RabbitMqInventoryConfig:
    sms_id = f"{site}-SMS"
    external = external_pile or f"EXT-{site}"
    return RabbitMqInventoryConfig(
        site_id=site,
        sms_id=sms_id,
        enabled=True,
        amqp_url_env=AMQP_ENV,
        request_exchange="yardmaster.read",
        request_routing_key=f"{site.lower()}.space-value.request",
        profile=_profile(site, sms_id),
        external_to_model={external: model_pile},
        timeout_s=2.0,
        tls_required=True,
        profile_revision="test-icd-v1",
    )


def _config_document(site: str = "MPH", model_pile: str = "HP-01") -> dict:
    """JSON-shaped equivalent of :func:`_config` for loader strictness tests."""
    config = _config(site, model_pile)
    return {
        "schema_version": "1.0",
        "sites": {
            site: {
                "enabled": True,
                "sms_id": config.sms_id,
                "profile_revision": config.profile_revision,
                "connection": {
                    "amqp_url_env": config.amqp_url_env,
                    "request_exchange": config.request_exchange,
                    "request_routing_key": config.request_routing_key,
                    "timeout_s": config.timeout_s,
                    "connect_timeout_s": config.connect_timeout_s,
                    "socket_timeout_s": config.socket_timeout_s,
                    "heartbeat_s": config.heartbeat_s,
                    "tls": {
                        "required": config.tls_required,
                        "ca_file_env": "",
                        "cert_file_env": "",
                        "key_file_env": "",
                        "server_name": "",
                    },
                },
                "limits": {
                    "max_body_bytes": config.max_body_bytes,
                    "max_rows": config.max_rows,
                    "max_response_skew_seconds":
                        config.max_response_skew_seconds,
                },
                "external_to_model": dict(config.external_to_model),
                "space_value_profile": asdict(config.profile),
            }
        },
    }


def _response_factory(
        *,
        captured_at: str | datetime = CAPTURED,
        response_correlation: Optional[str] = None,
        body_correlation: Optional[str] = None,
        response_pile: Optional[str] = None,
        wire_site: Optional[str] = None,
        wire_sms: Optional[str] = None,
) -> Callable[[RabbitMqInventoryConfig, OutboundMessage], InboundMessage]:
    captured_text = (captured_at.isoformat()
                     if isinstance(captured_at, datetime) else captured_at)

    def build(config: RabbitMqInventoryConfig,
              request: OutboundMessage) -> InboundMessage:
        external = response_pile or request.context["external_pile"]
        body = {
            "meta": {
                "capturedAt": captured_text,
                "correlationId": body_correlation or request.correlation_id,
                "type": "SpaceValueResponse",
                "version": "1.0",
                "site": wire_site or config.site_id,
                "sms": wire_sms or config.sms_id,
            },
            "segments": [
                {
                    "pileId": external,
                    "xFromCm": 0,
                    "xToCm": 100,
                    "massKg": 10_000,
                    "gradeCode": "WIRE-G",
                    "quality": {"bpl": 70.0},
                    "ageHours": 48,
                },
                {
                    "pileId": external,
                    "xFromCm": 100,
                    "xToCm": 200,
                    "massKg": 20_000,
                    "gradeCode": "WIRE-G",
                    "quality": {"bpl": 72.0},
                    "ageHours": 24,
                },
            ],
        }
        return InboundMessage(
            correlation_id=response_correlation or request.correlation_id,
            body=json.dumps(body).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            message_type="SpaceValueResponse",
        )

    return build


def _connector(config: RabbitMqInventoryConfig,
               transport: FakeRpcTransport) -> RabbitMqInventoryConnector:
    ids = itertools.count(1)
    return RabbitMqInventoryConnector(
        config,
        transport=transport,
        environ={AMQP_ENV: AMQP_VALUE},
        now=lambda: NOW,
        id_factory=lambda: f"{config.site_id}-corr-{next(ids)}",
    )


def _three_site_scenario() -> Scenario:
    sites_and_piles = (
        ("LOUTA", "LT-P1"),
        ("BENGUERIR", "BG-P1"),
        ("MPH", "HP-01"),
    )
    return Scenario(
        name="three-site live inventory contract test",
        quality_params=[QualityParam("BPL", "%")],
        sites=[Site(site, site.title(), "hub" if site == "MPH" else "mine")
               for site, _ in sites_and_piles],
        grades=[Grade("G", "Test grade", {"BPL": 70.0})],
        spaces=[
            StockSpace(
                pile, site, "test-yard", "open", capacity_t=100.0,
                mass_t=5.0, grade="G", quality={"BPL": 69.0},
                x_from=0.0, x_to=2.0,
            )
            for site, pile in sites_and_piles
        ],
    )


def _three_site_connectors(
        scenario: Scenario,
        capture_times: Mapping[str, datetime],
        *,
        failed_site: str = "",
) -> dict[str, RabbitMqInventoryConnector]:
    connectors: dict[str, RabbitMqInventoryConnector] = {}
    for space in scenario.spaces:
        config = _config(space.site, space.id)
        if space.site == failed_site:
            transport = FakeRpcTransport(
                call_error=LiveInventoryTimeout("SpaceValueResponse deadline"))
        else:
            transport = FakeRpcTransport(_response_factory(
                captured_at=capture_times[space.site]))
        connectors[space.site] = _connector(config, transport)
    return connectors


def test_valid_per_pile_segment_fetch_and_normalisation():
    config = _config()
    transport = FakeRpcTransport(_response_factory())
    response = _connector(config, transport).fetch(timeout_s=1.25)

    assert response.site_id == "MPH"
    assert response.sms_id == "MPH-SMS"
    assert response.captured_at == CAPTURED
    assert response.received_at == NOW
    assert response.correlation_ids == ("MPH-corr-1",)
    assert len(transport.calls) == 1
    _, requests, timeout = transport.calls[0]
    assert timeout == pytest.approx(1.25)
    assert json.loads(requests[0].body) == {
        "requestId": "MPH-corr-1",
        "site": "MPH",
        "sms": "MPH-SMS",
        "pileId": "EXT-MPH",
    }

    assert list(response.rows) == [
        {
            "site": "MPH", "pile": "HP-01", "record_type": "segment",
            "mass_t": 10.0, "captured_at": CAPTURED.isoformat(),
            "grade": "G", "quality": {"BPL": 70.0},
            "x_from": 0.0, "x_to": 1.0, "age_days": 2.0,
        },
        {
            "site": "MPH", "pile": "HP-01", "record_type": "segment",
            "mass_t": 20.0, "captured_at": CAPTURED.isoformat(),
            "grade": "G", "quality": {"BPL": 72.0},
            "x_from": 1.0, "x_to": 2.0, "age_days": 1.0,
        },
    ]


def test_server_capture_timestamp_must_include_timezone():
    connector = _connector(
        _config(),
        FakeRpcTransport(_response_factory(
            captured_at="2026-07-22T11:50:00")),
    )

    with pytest.raises(LiveInventoryProtocolError,
                       match="server captured_at has no timezone"):
        connector.fetch()


def test_amqp_correlation_mismatch_is_rejected():
    connector = _connector(
        _config(),
        FakeRpcTransport(_response_factory(
            response_correlation="unexpected-correlation")),
    )

    with pytest.raises(LiveInventoryProtocolError,
                       match="AMQP correlation_id mismatch"):
        connector.fetch()


def test_response_for_a_different_pile_is_rejected():
    connector = _connector(
        _config(),
        FakeRpcTransport(_response_factory(response_pile="EXT-OTHER")),
    )

    with pytest.raises(LiveInventorySiteError,
                       match="does not match requested pile"):
        connector.fetch()


def test_wire_site_mismatch_is_rejected():
    connector = _connector(
        _config(),
        FakeRpcTransport(_response_factory(wire_site="BENGUERIR")),
    )

    with pytest.raises(LiveInventoryProtocolError, match="wire site mismatch"):
        connector.fetch()


def test_transport_timeout_is_propagated_without_wrapping():
    transport = FakeRpcTransport(
        call_error=LiveInventoryTimeout("SpaceValueResponse deadline"))
    connector = _connector(_config(), transport)

    with pytest.raises(LiveInventoryTimeout,
                       match="SpaceValueResponse deadline") as caught:
        connector.fetch(timeout_s=0.25)

    assert caught.value.code == "RESPONSE_TIMEOUT"
    assert transport.calls[0][2] == pytest.approx(0.25)


def test_secret_bearing_config_is_rejected_and_secret_never_leaks():
    embedded = {
        "schema_version": "1.0",
        "sites": {
            "MPH": {
                "connection": {
                    "amqp_url_env": AMQP_ENV,
                    "password": SENTINEL_SECRET,
                }
            }
        },
    }
    bundle = load_live_inventory_configs({
        CONFIG_JSON_ENV: json.dumps(embedded),
    })

    assert bundle.configs == {}
    assert bundle.issues, "embedded credential-bearing input must be rejected"
    assert SENTINEL_SECRET not in repr(bundle)
    assert SENTINEL_SECRET not in " ".join(bundle.issues)

    config = _config()
    secret_environ = {AMQP_ENV: SENTINEL_SECRET}
    connector = RabbitMqInventoryConnector(
        config,
        transport=FakeRpcTransport(_response_factory()),
        environ=secret_environ,
        now=lambda: NOW,
        id_factory=lambda: "safe-id",
    )
    assert SENTINEL_SECRET not in repr(config)
    assert SENTINEL_SECRET not in repr(connector)
    assert SENTINEL_SECRET not in json.dumps(
        connector.readiness(), sort_keys=True)

    invalid = replace(config, request_routing_key="")
    invalid_connector = RabbitMqInventoryConnector(
        invalid,
        transport=FakeRpcTransport(_response_factory()),
        environ=secret_environ,
        now=lambda: NOW,
    )
    with pytest.raises(LiveInventoryConfigError) as caught:
        invalid_connector.fetch()
    assert SENTINEL_SECRET not in str(caught.value)


def test_direct_amqp_url_cannot_masquerade_as_env_ref_or_leak():
    bad_ref = f"amqps://user:{SENTINEL_SECRET}@broker.invalid/vhost"
    document = _config_document()
    document["sites"]["MPH"]["connection"]["amqp_url_env"] = bad_ref

    bundle = load_live_inventory_configs({
        CONFIG_JSON_ENV: json.dumps(document),
    })

    assert bundle.configs == {}
    assert bundle.issues, "a literal URL in an *_env field must be rejected"
    assert SENTINEL_SECRET not in repr(bundle)
    assert SENTINEL_SECRET not in " ".join(bundle.issues)

    # Direct construction is also defensive: repr/readiness/errors must not
    # turn a commissioning mistake into a credential disclosure.
    config = replace(_config(), amqp_url_env=bad_ref)
    connector = RabbitMqInventoryConnector(
        config,
        transport=FakeRpcTransport(_response_factory()),
        environ={},
        now=lambda: NOW,
    )
    errors = config.validation_errors({})
    assert errors
    assert SENTINEL_SECRET not in repr(config)
    assert SENTINEL_SECRET not in repr(connector.readiness())
    assert SENTINEL_SECRET not in " ".join(errors)


def test_unknown_site_configuration_keys_are_rejected_not_ignored():
    document = _config_document()
    document["sites"]["MPH"]["unexpected_auth_blob"] = SENTINEL_SECRET

    bundle = load_live_inventory_configs({
        CONFIG_JSON_ENV: json.dumps(document),
    })

    assert bundle.configs == {}
    assert any("unknown" in issue.lower() for issue in bundle.issues)
    assert SENTINEL_SECRET not in repr(bundle)
    assert SENTINEL_SECRET not in " ".join(bundle.issues)


def test_per_pile_request_template_must_select_the_pile():
    profile = replace(
        _profile("MPH", "MPH-SMS"),
        request_template={"requestId": "{request_id}"},
    )
    config = replace(_config(), profile=profile)

    errors = config.validation_errors({AMQP_ENV: AMQP_VALUE})

    assert any("{pile_id}" in error for error in errors)
    assert RabbitMqInventoryConnector(
        config,
        transport=FakeRpcTransport(_response_factory()),
        environ={AMQP_ENV: AMQP_VALUE},
    ).readiness()["ready"] is False


@pytest.mark.parametrize(
    ("scope", "field_name", "value"),
    [
        ("profile", "mass_scale_to_t", float("nan")),
        ("profile", "mass_scale_to_t", float("inf")),
        ("profile", "x_scale_to_m", float("nan")),
        ("profile", "age_scale_to_days", float("inf")),
        ("config", "timeout_s", float("nan")),
        ("config", "connect_timeout_s", float("inf")),
        ("config", "socket_timeout_s", float("nan")),
        ("config", "max_response_skew_seconds", float("inf")),
    ],
)
def test_non_finite_connector_configuration_is_rejected(
        scope: str, field_name: str, value: float):
    config = _config()
    if scope == "profile":
        config = replace(
            config, profile=replace(config.profile, **{field_name: value}))
    else:
        config = replace(config, **{field_name: value})

    errors = config.validation_errors({AMQP_ENV: AMQP_VALUE})

    assert errors, f"{scope}.{field_name}={value!r} was accepted"
    assert RabbitMqInventoryConnector(
        config,
        transport=FakeRpcTransport(_response_factory()),
        environ={AMQP_ENV: AMQP_VALUE},
    ).readiness()["ready"] is False


def test_scaled_numeric_overflow_returns_protocol_issue_not_uncaught_error():
    scenario = _three_site_scenario()
    before = copy.deepcopy(asdict(scenario))
    config = _config("MPH", "HP-01")
    config = replace(
        config,
        profile=replace(config.profile, mass_scale_to_t=1e308),
    )
    connector = _connector(
        config, FakeRpcTransport(_response_factory(captured_at=CAPTURED)))

    result = pull_live_inventory_snapshot(
        scenario, {"MPH": connector}, sites=["MPH"],
        require_complete=False, now=NOW, max_age_hours=1,
    )

    assert result.snapshot is None
    assert any(issue.code == "PROTOCOL_ERROR" and issue.site == "MPH"
               for issue in result.issues)
    assert asdict(scenario) == before


def test_configured_tls_env_refs_must_exist_and_cert_key_must_pair():
    base = _config()

    missing_ca = replace(base, tls_ca_file_env="SMS_CA_FILE")
    assert any("SMS_CA_FILE" in error for error in
               missing_ca.validation_errors({AMQP_ENV: AMQP_VALUE}))

    unpaired = replace(base, tls_cert_file_env="SMS_CLIENT_CERT")
    unpaired_errors = unpaired.validation_errors({
        AMQP_ENV: AMQP_VALUE,
        "SMS_CLIENT_CERT": "C:/protected/client.crt",
    })
    assert any("certificate and key" in error for error in unpaired_errors)

    pair_with_missing_value = replace(
        base,
        tls_cert_file_env="SMS_CLIENT_CERT",
        tls_key_file_env="SMS_CLIENT_KEY",
    )
    pair_errors = pair_with_missing_value.validation_errors({
        AMQP_ENV: AMQP_VALUE,
        "SMS_CLIENT_CERT": "C:/protected/client.crt",
    })
    assert any("SMS_CLIENT_KEY" in error for error in pair_errors)

    complete_env = {
        AMQP_ENV: AMQP_VALUE,
        "SMS_CA_FILE": "C:/protected/ca.crt",
        "SMS_CLIENT_CERT": "C:/protected/client.crt",
        "SMS_CLIENT_KEY": "C:/protected/client.key",
    }
    complete = replace(
        base,
        tls_ca_file_env="SMS_CA_FILE",
        tls_cert_file_env="SMS_CLIENT_CERT",
        tls_key_file_env="SMS_CLIENT_KEY",
    )
    assert complete.validation_errors(complete_env) == []


@pytest.mark.parametrize("boolean_path", [
    "enabled", "tls.required", "profile.records_are_segments",
])
def test_string_booleans_are_rejected_instead_of_truthiness_coerced(
        boolean_path: str):
    document = _config_document()
    site = document["sites"]["MPH"]
    if boolean_path == "enabled":
        site["enabled"] = "false"
    elif boolean_path == "tls.required":
        site["connection"]["tls"]["required"] = "false"
    else:
        site["space_value_profile"]["records_are_segments"] = "false"

    bundle = load_live_inventory_configs({
        CONFIG_JSON_ENV: json.dumps(document),
    })

    assert "MPH" not in bundle.configs
    assert bundle.issues


def test_live_profile_requires_wire_identity_and_protocol_version():
    profile = replace(
        _profile("MPH", "MPH-SMS"),
        body_version_path="",
        body_site_path="",
        body_sms_path="",
        expected_protocol_version="",
        expected_wire_site="",
        expected_wire_sms="",
    )

    errors = profile.validation_errors()

    assert any("protocol" in error.lower() and "version" in error.lower()
               for error in errors)
    assert any("identity" in error.lower() and
               ("site" in error.lower() or "sms" in error.lower())
               for error in errors)


def test_optional_pika_missing_health_is_actionable_without_import_failure():
    transport = FakeRpcTransport(probe_error=OptionalDependencyMissing(
        "RabbitMQ client is not installed; install requirements-rabbitmq.txt"))
    connector = _connector(_config(), transport)

    health = connector.health(timeout_s=0.4)

    assert health["ready"] is True
    assert health["reachable"] is False
    assert health["mode"] == "error"
    assert health["error_code"] == "OPTIONAL_DEPENDENCY_MISSING"
    assert "requirements-rabbitmq.txt" in health["detail"]
    assert transport.probes == [("MPH", 0.4)]


def test_cross_site_model_mapping_is_rejected_atomically():
    scenario = _three_site_scenario()
    before = copy.deepcopy(asdict(scenario))
    # A Louta connector is deliberately configured to decode into a
    # Benguerir model pile.  The codec accepts only the explicit map; the
    # three-site combiner is the final site-isolation guard.
    config = _config("LOUTA", "BG-P1")
    connectors = {
        "LOUTA": _connector(
            config, FakeRpcTransport(_response_factory(captured_at=CAPTURED)))
    }

    result = pull_live_inventory_snapshot(
        scenario, connectors, sites=["LOUTA"], require_complete=False,
        now=NOW, max_age_hours=1,
    )

    assert result.snapshot is None
    assert any(i.code == "CROSS_SITE_MAPPING" and i.site == "LOUTA"
               for i in result.issues)
    assert asdict(scenario) == before


def test_three_site_pull_combines_segments_into_one_atomic_snapshot():
    scenario = _three_site_scenario()
    before = copy.deepcopy(asdict(scenario))
    capture_times = {
        "LOUTA": CAPTURED,
        "BENGUERIR": CAPTURED + timedelta(seconds=10),
        "MPH": CAPTURED + timedelta(seconds=20),
    }
    connectors = _three_site_connectors(scenario, capture_times)

    result = pull_live_inventory_snapshot(
        scenario, connectors, now=NOW, max_age_hours=1,
        max_site_skew_seconds=60,
    )

    assert result.ok
    assert result.snapshot is not None
    assert set(result.responses) == {"LOUTA", "BENGUERIR", "MPH"}
    assert result.requested_sites == ["BENGUERIR", "LOUTA", "MPH"]
    assert result.snapshot.captured_at == capture_times["MPH"]
    assert result.snapshot.acquisition_mode == "live-read-only"
    assert result.snapshot.site_captured_at == {
        site: value.isoformat() for site, value in capture_times.items()
    }
    assert set(result.snapshot.pile_ids) == {"LT-P1", "BG-P1", "HP-01"}
    for pile in result.snapshot.piles:
        assert pile.mass_t is None
        assert len(pile.segments) == 2
        assert sum(segment.mass_t for segment in pile.segments) == \
            pytest.approx(30.0)
        assert [(segment.x_from, segment.x_to)
                for segment in pile.segments] == [(0.0, 1.0), (1.0, 2.0)]
    assert not [issue for issue in result.issues
                if issue.severity == "ERROR"]
    assert asdict(scenario) == before, "a read-only pull mutated the scenario"


def test_three_site_timestamp_skew_rejects_the_whole_snapshot():
    scenario = _three_site_scenario()
    before = copy.deepcopy(asdict(scenario))
    capture_times = {
        "LOUTA": CAPTURED,
        "BENGUERIR": CAPTURED + timedelta(seconds=15),
        "MPH": CAPTURED + timedelta(seconds=121),
    }
    connectors = _three_site_connectors(scenario, capture_times)

    result = pull_live_inventory_snapshot(
        scenario, connectors, now=NOW, max_age_hours=1,
        max_site_skew_seconds=60,
    )

    assert result.snapshot is None
    assert set(result.responses) == {"LOUTA", "BENGUERIR", "MPH"}
    assert any(issue.code == "SITE_TIMESTAMP_SKEW"
               for issue in result.issues)
    assert asdict(scenario) == before


def test_one_site_failure_returns_no_snapshot_and_no_scenario_mutation():
    scenario = _three_site_scenario()
    before = copy.deepcopy(asdict(scenario))
    capture_times = {site: CAPTURED for site in
                     ("LOUTA", "BENGUERIR", "MPH")}
    connectors = _three_site_connectors(
        scenario, capture_times, failed_site="BENGUERIR")

    result = pull_live_inventory_snapshot(
        scenario, connectors, now=NOW, max_age_hours=1,
    )

    assert result.snapshot is None
    assert set(result.responses) == {"LOUTA", "MPH"}
    assert any(issue.code == "RESPONSE_TIMEOUT" and
               issue.site == "BENGUERIR" for issue in result.issues)
    assert asdict(scenario) == before
