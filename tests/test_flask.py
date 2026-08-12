from collections.abc import Sequence

from apirelio import ApirelioEvent
from flask import Flask, jsonify

from apirelio_flask import get_request_context, install_apirelio


class RecordingTransport:
    def __init__(self) -> None:
        self.events: list[ApirelioEvent] = []

    def send(self, events: Sequence[ApirelioEvent]) -> None:
        self.events.extend(events)


def test_captures_resolved_route_customer_application_and_context() -> None:
    transport = RecordingTransport()
    app = Flask(__name__)
    client = install_apirelio(
        app,
        api_key="apr_test",
        service="flask-api",
        environment="test",
        transport=transport,
        metadata_keys=("region",),
        resolve_customer=lambda current: {
            "id": current.headers.get("x-customer-id", "unknown"),
            "plan": "growth",
        },
        resolve_application=lambda current: {
            "id": current.headers.get("x-application-id", "unknown")
        },
    )

    @app.get("/customers/<int:customer_id>")
    def customer(customer_id: int):  # type: ignore[no-untyped-def]
        context = get_request_context()
        assert context is not None
        context.add_metadata({"region": "eu", "token": "never"})
        return jsonify(id=customer_id)

    response = app.test_client().get(
        "/customers/42?secret=nope",
        headers={"x-customer-id": "acme", "x-application-id": "billing"},
    )
    assert response.status_code == 200
    assert client.flush()

    event = transport.events[0]
    assert event["route"] == "/customers/{customer_id}"
    assert event["route_name"] == "customer"
    assert event["customer_id"] == "acme"
    assert event["application_id"] == "billing"
    assert event["metadata"]["region"] == "eu"
    assert "token" not in event["metadata"]
    assert event["sdk"] == "flask"
    assert "secret" not in str(event)


def test_filters_routes() -> None:
    transport = RecordingTransport()
    app = Flask(__name__)
    client = install_apirelio(
        app,
        api_key="apr_test",
        service="flask-api",
        environment="test",
        transport=transport,
        include_routes=("/api/**",),
        exclude_routes=("/api/internal/**",),
    )

    app.add_url_rule("/api/orders/<order_id>", "order", lambda order_id: order_id)
    app.add_url_rule("/api/internal/jobs", "jobs", lambda: "ok")
    app.add_url_rule("/health", "health", lambda: "ok")

    browser = app.test_client()
    browser.get("/api/orders/42")
    browser.get("/api/internal/jobs")
    browser.get("/health")
    assert client.flush()

    assert [event["route"] for event in transport.events] == ["/api/orders/{order_id}"]


def test_records_unhandled_exception_without_swallowing_it() -> None:
    transport = RecordingTransport()
    app = Flask(__name__)
    client = install_apirelio(
        app,
        api_key="apr_test",
        service="flask-api",
        environment="test",
        transport=transport,
    )

    @app.get("/explode")
    def explode() -> str:
        raise ValueError("private message")

    response = app.test_client().get("/explode")
    assert response.status_code == 500
    assert client.flush()
    assert transport.events[0]["status"] == 500
    assert transport.events[0]["error_code"] == "ValueError"
    assert "private message" not in str(transport.events[0])


def test_resolver_failure_never_changes_response() -> None:
    transport = RecordingTransport()
    app = Flask(__name__)

    def broken_resolver(current):  # type: ignore[no-untyped-def]
        raise RuntimeError("resolver failed")

    client = install_apirelio(
        app,
        api_key="apr_test",
        service="flask-api",
        environment="test",
        transport=transport,
        resolve_customer=broken_resolver,
    )
    app.add_url_rule("/ok", "ok", lambda: "customer response")

    response = app.test_client().get("/ok")
    assert response.status_code == 200
    assert response.text == "customer response"
    assert client.flush()
    assert transport.events[0]["customer_id"] is None
