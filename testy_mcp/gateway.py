import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.urls import Resolver404, resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from testy_mcp.errors import GatewayError

_SAFE_API_PATH = re.compile(r"^/api/v2/[a-z0-9][a-z0-9_\-/]*/?$")
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(slots=True)
class GatewayResponse:
    status: int
    data: Any


class TestyAPIGateway:
    """Dispatch a request to TestY's own DRF views without a network round trip."""

    def __init__(self, user, auth=None, *, host: str = "testserver", secure: bool = False):
        self.user = user
        self.auth = auth
        self.host = host
        self.secure = secure
        self.factory = APIRequestFactory()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        check_size: bool = True,
    ) -> GatewayResponse:
        method = method.upper()
        normalized_path = self._validate_path(path)
        if method not in _ALLOWED_METHODS:
            raise GatewayError(f"HTTP method {method!r} is not allowed")

        query_string = self._encode_query(query or {})
        request_path = normalized_path
        if query_string:
            request_path = f"{normalized_path}?{query_string}"

        request = self.factory.generic(
            method,
            request_path,
            data=b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            secure=self.secure,
            HTTP_HOST=self.host,
        )
        force_authenticate(request, user=self.user, token=self.auth)

        try:
            match = resolve(normalized_path)
        except Resolver404 as exc:
            raise GatewayError("TestY API endpoint was not found", status=404) from exc

        response = match.func(request, *match.args, **match.kwargs)
        if hasattr(response, "render"):
            response.render()

        data = getattr(response, "data", None)
        if data is None:
            content = getattr(response, "content", b"")
            if content:
                try:
                    data = json.loads(content)
                except (TypeError, ValueError):
                    data = content.decode("utf-8", errors="replace")

        if check_size:
            max_bytes = getattr(settings, "TESTY_MCP_MAX_RESPONSE_BYTES", 1024 * 1024)
            encoded = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            if len(encoded) > max_bytes:
                raise GatewayError(
                    "TestY API response is too large; narrow the query with filters or pagination",
                    status=413,
                )

        return GatewayResponse(status=response.status_code, data=data)

    @staticmethod
    def _validate_path(path: str) -> str:
        if not isinstance(path, str):
            raise GatewayError("path must be a string")
        normalized = "/" + path.lstrip("/")
        if ".." in normalized or not _SAFE_API_PATH.fullmatch(normalized):
            raise GatewayError("Only safe TestY /api/v2/ paths are allowed")
        return normalized

    @staticmethod
    def _encode_query(query: dict[str, Any]) -> str:
        if not isinstance(query, dict):
            raise GatewayError("query must be an object")
        items: list[tuple[str, Any]] = []
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                items.extend((str(key), item) for item in value)
            elif isinstance(value, bool):
                items.append((str(key), str(value).lower()))
            elif isinstance(value, (dict, set)):
                items.append((str(key), json.dumps(value, ensure_ascii=False)))
            else:
                items.append((str(key), value))
        return urlencode(items, doseq=True)
