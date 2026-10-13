import base64
import binascii
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from testy_mcp.errors import FORBIDDEN_ORIGIN_ERROR
from testy_mcp.protocol import MODERN_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS


@dataclass(slots=True)
class TransportError(Exception):
    code: int
    message: str
    http_status: int = 400
    data: Any = None


def validate_origin(
    origin: str | None,
    server_origin: str,
    allowed_origins: list[str] | tuple[str, ...] | set[str] = (),
) -> None:
    """Validate an HTTP Origin using exact, normalized origin comparison."""

    if origin is None:
        return
    normalized = _normalize_origin(origin)
    allowed = {_normalize_origin(server_origin)}
    allowed.update(_normalize_origin(item) for item in allowed_origins)
    if normalized is None or normalized not in allowed:
        raise TransportError(FORBIDDEN_ORIGIN_ERROR, "Forbidden Origin", http_status=403)


def validate_request_headers(
    payload: dict[str, Any], headers: Mapping[str, str]
) -> str | None:
    """Validate Streamable HTTP metadata and return the request protocol version.

    Legacy initialize-based requests are intentionally accepted without the modern
    mirrored headers. Modern requests must carry matching body and HTTP metadata.
    """

    method = payload.get("method")
    params = payload.get("params", {})
    metadata = params.get("_meta", {}) if isinstance(params, dict) else {}
    body_version = (
        metadata.get("io.modelcontextprotocol/protocolVersion")
        if isinstance(metadata, dict)
        else None
    )
    header_version = headers.get("MCP-Protocol-Version")

    is_modern_attempt = (
        body_version == MODERN_PROTOCOL_VERSION
        or header_version == MODERN_PROTOCOL_VERSION
    )
    if is_modern_attempt:
        if header_version != body_version:
            raise _header_mismatch(
                "MCP-Protocol-Version header does not match request metadata"
            )
        if header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise _unsupported_version(header_version)
        if not isinstance(method, str):
            return header_version
        method_header = headers.get("Mcp-Method")
        if method_header != method:
            raise _header_mismatch(
                "Mcp-Method header does not match the JSON-RPC method"
            )
        _validate_name_header(method, params, headers)
        return header_version

    requested_version = header_version or body_version
    if (
        requested_version is not None
        and requested_version not in SUPPORTED_PROTOCOL_VERSIONS
    ):
        raise _unsupported_version(requested_version)
    return header_version


def error_payload(request_id: Any, error: TransportError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.data is not None:
        body["data"] = error.data
    return {"jsonrpc": "2.0", "id": request_id, "error": body}


def _validate_name_header(method: str, params: Any, headers: Mapping[str, str]) -> None:
    source_key = {
        "tools/call": "name",
        "prompts/get": "name",
        "resources/read": "uri",
    }.get(method)
    if source_key is None:
        return
    name_header = headers.get("Mcp-Name")
    if name_header is None:
        raise _header_mismatch("Required Mcp-Name header is missing")
    try:
        decoded = _decode_header_value(name_header)
    except ValueError as exc:
        raise _header_mismatch("Mcp-Name header is malformed") from exc
    body_name = params.get(source_key) if isinstance(params, dict) else None
    if not isinstance(body_name, str) or decoded != body_name:
        raise _header_mismatch("Mcp-Name header does not match the request body")


def _decode_header_value(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        encoded = value[len("=?base64?") : -2]
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("invalid Base64 header value") from exc
    if value != value.strip() or any(
        (ord(char) < 0x20 and ord(char) != 0x09) or ord(char) > 0x7E
        for char in value
    ):
        raise ValueError("invalid plain header value")
    return value


def tool_call_messages(payload: Any) -> list[dict[str, Any]]:
    """Return every tools/call message, including entries of a legacy batch."""

    if isinstance(payload, dict):
        return [payload] if payload.get("method") == "tools/call" else []
    if isinstance(payload, list):
        return [
            message
            for message in payload
            if isinstance(message, dict) and message.get("method") == "tools/call"
        ]
    return []


def batch_uses_modern_metadata(payload: list[Any]) -> bool:
    """Detect modern per-request metadata inside a JSON-RPC batch.

    JSON-RPC batching was removed in MCP 2025-06-18, so a batch must not carry
    the 2026-07-28 params._meta protocol fields.
    """

    for message in payload:
        if not isinstance(message, dict):
            continue
        params = message.get("params")
        if not isinstance(params, dict):
            continue
        metadata = params.get("_meta")
        if isinstance(metadata, dict) and (
            "io.modelcontextprotocol/protocolVersion" in metadata
        ):
            return True
    return False


def _unsupported_version(requested: Any) -> TransportError:
    return TransportError(
        -32022,
        "Unsupported protocol version",
        data={
            "supported": sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True),
            "requested": requested,
        },
    )


def _header_mismatch(message: str) -> TransportError:
    return TransportError(-32020, f"Header mismatch: {message}")


def _normalize_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    default_port = 80 if parsed.scheme == "http" else 443
    authority = parsed.hostname.lower()
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"
