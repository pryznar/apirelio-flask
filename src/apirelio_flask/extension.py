import atexit
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Optional, TypeVar

from apirelio import (
    ApirelioClient,
    Application,
    Customer,
    Environment,
    EventTransport,
    Metadata,
    normalize_route,
    should_capture_route,
)
from flask import Flask, Request, Response, has_request_context, request
from flask.signals import got_request_exception

SDK_VERSION = "0.1.0"
ResolverValue = TypeVar("ResolverValue")
Resolver = Callable[[Request], Optional[ResolverValue]]
ErrorCodeResolver = Callable[[Request, Optional[BaseException], Optional[Response]], Optional[str]]

_CONVERTER = re.compile(r"<(?:(?:[^>:]+):)?([^>]+)>")
_CONTEXT_KEY = "apirelio.request_context"
_STARTED_KEY = "apirelio.started_at"
_ERROR_KEY = "apirelio.error"
_CAPTURED_KEY = "apirelio.captured"
_clients: list[ApirelioClient] = []


@dataclass
class RequestContext:
    metadata: Metadata = field(default_factory=dict)
    error_code: Optional[str] = None

    def add_metadata(self, values: Metadata) -> "RequestContext":
        self.metadata.update(values)
        return self

    def set_error_code(self, value: Optional[str]) -> "RequestContext":
        self.error_code = value[:255] if value else None
        return self


class Apirelio:
    def __init__(
        self,
        app: Flask,
        *,
        client: ApirelioClient,
        include_routes: Sequence[str] = (),
        exclude_routes: Sequence[str] = (),
        resolve_customer: Optional[Resolver[Customer]] = None,
        resolve_application: Optional[Resolver[Application]] = None,
        resolve_error_code: Optional[ErrorCodeResolver] = None,
        resolve_metadata: Optional[Resolver[Metadata]] = None,
        api_version_header: str = "x-api-version",
        capture_headers: Sequence[str] = ("x-sdk-version", "user-agent"),
    ) -> None:
        self.client = client
        self.include_routes = tuple(include_routes)
        self.exclude_routes = tuple(exclude_routes)
        self.resolve_customer = resolve_customer
        self.resolve_application = resolve_application
        self.resolve_error_code = resolve_error_code
        self.resolve_metadata = resolve_metadata
        self.api_version_header = api_version_header.lower()
        self.capture_headers = tuple(name.lower() for name in capture_headers)

        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)
        got_request_exception.connect(self._record_exception, app, weak=False)
        app.extensions["apirelio"] = self

    def _before_request(self) -> None:
        request.environ[_STARTED_KEY] = time.perf_counter()
        request.environ[_CONTEXT_KEY] = RequestContext()

    def _after_request(self, response: Response) -> Response:
        self._capture(response, _stored_error())
        return response

    def _teardown_request(self, error: Optional[BaseException]) -> None:
        if not request.environ.get(_CAPTURED_KEY):
            self._capture(None, error or _stored_error())

    def _record_exception(self, sender: Flask, exception: BaseException, **extra: object) -> None:
        del sender, extra
        if has_request_context():
            request.environ[_ERROR_KEY] = exception

    def _capture(
        self, response: Optional[Response], error: Optional[BaseException]
    ) -> None:
        try:
            if request.environ.get(_CAPTURED_KEY):
                return
            request.environ[_CAPTURED_KEY] = True
            route = _route_template(request)
            if not should_capture_route(route, self.include_routes, self.exclude_routes):
                return

            context = get_request_context()
            metadata = _captured_headers(request.headers.items(), self.capture_headers)
            resolved_metadata = _resolve_safely(self.resolve_metadata, request)
            if resolved_metadata:
                metadata.update(resolved_metadata)
            if context:
                metadata.update(context.metadata)

            started_at = request.environ.get(_STARTED_KEY)
            duration = (
                (time.perf_counter() - started_at) * 1000
                if isinstance(started_at, float)
                else 0.0
            )
            self.client.capture(
                {
                    "method": request.method or "UNKNOWN",
                    "route": route,
                    "route_name": request.endpoint,
                    "status": response.status_code if response is not None else 500,
                    "duration_ms": duration,
                    "request_bytes": _string_integer(request.headers.get("content-length")),
                    "response_bytes": (
                        _string_integer(response.headers.get("content-length"))
                        if response is not None
                        else None
                    ),
                    "customer": _resolve_safely(self.resolve_customer, request),
                    "application": _resolve_safely(self.resolve_application, request),
                    "api_version": request.headers.get(self.api_version_header),
                    "sdk": "flask",
                    "sdk_version": SDK_VERSION,
                    "error_code": self._error_code(request, error, response, context),
                    "metadata": metadata,
                }
            )
        except BaseException:
            # Analytics must never alter the Flask response or exception.
            pass

    def _error_code(
        self,
        current_request: Request,
        error: Optional[BaseException],
        response: Optional[Response],
        context: Optional[RequestContext],
    ) -> Optional[str]:
        if context and context.error_code:
            return context.error_code
        if self.resolve_error_code:
            try:
                resolved = self.resolve_error_code(current_request, error, response)
                if resolved:
                    return str(resolved)[:255]
            except BaseException:
                pass
        return error.__class__.__name__[:255] if error else None


def install_apirelio(
    app: Flask,
    *,
    api_key: str,
    service: str,
    endpoint: str = "https://apirelio.com",
    environment: Environment = "production",
    release: Optional[str] = None,
    enabled: bool = True,
    batch_size: int = 100,
    flush_interval: float = 5.0,
    max_queue_size: int = 10_000,
    timeout: float = 2.0,
    max_retries: int = 2,
    metadata_keys: Sequence[str] = (),
    include_routes: Sequence[str] = (),
    exclude_routes: Sequence[str] = (),
    resolve_customer: Optional[Resolver[Customer]] = None,
    resolve_application: Optional[Resolver[Application]] = None,
    resolve_error_code: Optional[ErrorCodeResolver] = None,
    resolve_metadata: Optional[Resolver[Metadata]] = None,
    api_version_header: str = "x-api-version",
    capture_headers: Sequence[str] = ("x-sdk-version", "user-agent"),
    transport: Optional[EventTransport] = None,
) -> ApirelioClient:
    client = ApirelioClient(
        api_key=api_key,
        service=service,
        endpoint=endpoint,
        environment=environment,
        release=release,
        enabled=enabled,
        batch_size=batch_size,
        flush_interval=flush_interval,
        max_queue_size=max_queue_size,
        timeout=timeout,
        max_retries=max_retries,
        metadata_keys=metadata_keys,
        transport=transport,
    )
    Apirelio(
        app,
        client=client,
        include_routes=include_routes,
        exclude_routes=exclude_routes,
        resolve_customer=resolve_customer,
        resolve_application=resolve_application,
        resolve_error_code=resolve_error_code,
        resolve_metadata=resolve_metadata,
        api_version_header=api_version_header,
        capture_headers=capture_headers,
    )
    _clients.append(client)
    return client


def get_request_context() -> Optional[RequestContext]:
    if not has_request_context():
        return None
    value = request.environ.get(_CONTEXT_KEY)
    return value if isinstance(value, RequestContext) else None


def shutdown_apirelio(timeout: float = 5.0) -> bool:
    succeeded = True
    while _clients:
        client = _clients.pop()
        try:
            succeeded = client.shutdown(timeout) and succeeded
        except BaseException:
            succeeded = False
    return succeeded


def _resolve_safely(
    resolver: Optional[Resolver[ResolverValue]], current_request: Request
) -> Optional[ResolverValue]:
    try:
        return resolver(current_request) if resolver else None
    except BaseException:
        return None


def _route_template(current_request: Request) -> str:
    rule = current_request.url_rule
    if rule is not None:
        return normalize_route(_CONVERTER.sub(r"{\1}", rule.rule))
    return normalize_route(current_request.path)


def _captured_headers(
    headers: Iterable[tuple[str, str]], names: Sequence[str]
) -> Metadata:
    allowed = set(names)
    return {"header." + key.lower(): value for key, value in headers if key.lower() in allowed}


def _stored_error() -> Optional[BaseException]:
    value = request.environ.get(_ERROR_KEY)
    return value if isinstance(value, BaseException) else None


def _string_integer(value: Optional[str]) -> Optional[int]:
    return int(value) if value and value.isdigit() else None


atexit.register(shutdown_apirelio)
