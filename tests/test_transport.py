import base64

import pytest

from testy_mcp.protocol import MODERN_PROTOCOL_VERSION
from testy_mcp.transport import (
    TransportError,
    validate_origin,
    validate_request_headers,
)


def modern_request(method="tools/call", **params):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def test_modern_headers_are_validated():
    payload = modern_request(name="testy_list", arguments={"entity": "projects"})
    headers = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "testy_list",
    }

    assert validate_request_headers(payload, headers) == MODERN_PROTOCOL_VERSION


def test_missing_modern_method_header_is_rejected():
    payload = modern_request(method="ping")

    with pytest.raises(TransportError) as caught:
        validate_request_headers(
            payload, {"MCP-Protocol-Version": MODERN_PROTOCOL_VERSION}
        )

    assert caught.value.code == -32020


def test_base64_name_header_is_decoded_before_comparison():
    name = "инструмент"
    encoded = base64.b64encode(name.encode()).decode()
    payload = modern_request(name=name, arguments={})
    headers = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": f"=?base64?{encoded}?=",
    }

    assert validate_request_headers(payload, headers) == MODERN_PROTOCOL_VERSION


def test_unsupported_version_lists_supported_versions():
    payload = modern_request(method="ping")
    payload["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"

    with pytest.raises(TransportError) as caught:
        validate_request_headers(payload, {"MCP-Protocol-Version": "2099-01-01"})

    assert caught.value.code == -32022
    assert MODERN_PROTOCOL_VERSION in caught.value.data["supported"]


def test_origin_allows_same_origin_and_explicit_allowlist():
    validate_origin("https://testy.example.com", "https://testy.example.com")
    validate_origin(
        "https://agents.example.com",
        "https://testy.example.com",
        ["https://agents.example.com"],
    )


def test_origin_rejects_untrusted_origin():
    with pytest.raises(TransportError) as caught:
        validate_origin("https://evil.example", "https://testy.example.com")

    assert caught.value.http_status == 403


def test_origin_error_uses_an_application_defined_code():
    from testy_mcp.errors import FORBIDDEN_ORIGIN_ERROR

    with pytest.raises(TransportError) as caught:
        validate_origin("https://evil.example", "https://testy.example.com")

    assert caught.value.code == FORBIDDEN_ORIGIN_ERROR


def test_plain_name_header_allows_horizontal_tab():
    payload = modern_request(name="a\tb", arguments={})
    headers = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "a\tb",
    }

    assert validate_request_headers(payload, headers) == MODERN_PROTOCOL_VERSION


def test_base64_sentinel_that_does_not_decode_is_rejected():
    payload = modern_request(name="tool", arguments={})
    headers = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "=?base64?not-valid-base64!!?=",
    }

    with pytest.raises(TransportError) as caught:
        validate_request_headers(payload, headers)

    assert caught.value.code == -32020


def test_batch_uses_modern_metadata_detection():
    from testy_mcp.transport import batch_uses_modern_metadata

    legacy = [{"jsonrpc": "2.0", "method": "tools/list"}]
    hidden_modern = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "testy_list",
                "arguments": {"entity": "projects"},
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION
                },
            },
        }
    ]

    assert batch_uses_modern_metadata(legacy) is False
    assert batch_uses_modern_metadata(hidden_modern) is True


def test_prefix_only_sentinel_is_treated_as_literal():
    name = "=?base64?literal"
    payload = modern_request(name=name, arguments={})
    headers = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
    }

    assert validate_request_headers(payload, headers) == MODERN_PROTOCOL_VERSION


def test_tool_call_messages_covers_single_and_batch_payloads():
    from testy_mcp.transport import tool_call_messages

    single = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}
    batch = [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "y"}},
        "not-a-message",
    ]

    assert tool_call_messages(single) == [single]
    assert tool_call_messages(batch) == [batch[1]]
    assert tool_call_messages(None) == []
    assert tool_call_messages({"method": "tools/list"}) == []
