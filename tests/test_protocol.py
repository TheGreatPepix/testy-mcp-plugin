import json
from dataclasses import dataclass

from testy_mcp.protocol import (
    LATEST_LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    MCPProtocol,
)


@dataclass
class Response:
    status: int
    data: object


class FakeGateway:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *, query=None, body=None):
        self.calls.append((method, path, query, body))
        return Response(
            200, {"method": method, "path": path, "query": query, "body": body}
        )


def request(method, params=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def modern_params(**values):
    return {
        **values,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }


def test_initialize_negotiates_supported_version():
    response = MCPProtocol().handle(
        request("initialize", {"protocolVersion": "2025-06-18"}),
        FakeGateway(),
    )

    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert response["result"]["serverInfo"]["name"] == "testy-mcp"


def test_instructions_announce_ai_labels_and_the_delete_rule():
    response = MCPProtocol().handle(
        request("initialize", {"protocolVersion": "2025-06-18"}),
        FakeGateway(),
    )

    instructions = response["result"]["instructions"]
    for label in ("'AI-generated'", "'AI-edited'", "'AI-copy'"):
        assert label in instructions
    assert "never delete anything without the user's explicit confirmation" in instructions


def test_initialize_falls_back_to_latest_version():
    response = MCPProtocol().handle(
        request("initialize", {"protocolVersion": "unsupported"}),
        FakeGateway(),
    )

    assert response["result"]["protocolVersion"] == LATEST_LEGACY_PROTOCOL_VERSION


def test_modern_discovery_advertises_current_protocol():
    response = MCPProtocol().handle(
        request("server/discover", modern_params()),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )
    result = response["result"]

    assert result["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert result["resultType"] == "complete"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "testy-mcp"


def test_discovery_without_modern_metadata_is_rejected():
    response = MCPProtocol().handle(request("server/discover"), FakeGateway())

    assert response["error"]["code"] == -32602


def test_modern_tool_list_has_cache_and_result_metadata():
    response = MCPProtocol().handle(
        request(
            "tools/list",
            {
                **modern_params(),
            },
        ),
        FakeGateway(),
    )
    result = response["result"]

    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 3600000
    assert result["cacheScope"] == "private"


def test_modern_protocol_can_be_selected_by_http_header():
    response = MCPProtocol().handle(
        request("tools/list", modern_params()),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert response["result"]["resultType"] == "complete"


def test_tools_list_exposes_crud_and_escape_hatch():
    response = MCPProtocol().handle(request("tools/list"), FakeGateway())
    names = {tool["name"] for tool in response["result"]["tools"]}

    assert names == {
        "testy_list",
        "testy_get",
        "testy_create",
        "testy_update",
        "testy_delete",
        "testy_action",
        "testy_api_request",
    }


def test_tool_definitions_carry_usage_rules_without_repeating_them():
    tools = {
        tool["name"]: tool
        for tool in MCPProtocol().handle(request("tools/list"), FakeGateway())["result"]["tools"]
    }
    payload = json.dumps(list(tools.values()))

    assert "page_size defaults to 25" in tools["testy_list"]["description"]
    for name in ("testy_create", "testy_update"):
        assert "do not re-read" in tools[name]["description"], name
    assert "never delete without the user's explicit confirmation" in (
        tools["testy_delete"]["description"]
    )
    assert payload.count("Test design: projects") == 1
    assert payload.count("Lists are projected element-wise") == 1
    assert payload.count("labels_condition") == 1


def test_tool_call_returns_structured_content():
    gateway = FakeGateway()
    response = MCPProtocol().handle(
        request(
            "tools/call",
            {
                "name": "testy_list",
                "arguments": {
                    "entity": "cases",
                    "query": {"project": 7, "page": 2, "page_size": 50},
                },
            },
        ),
        gateway,
    )

    assert gateway.calls == [
        ("GET", "/api/v2/cases/", {"project": 7, "page": 2, "page_size": 50}, None)
    ]
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["path"] == "/api/v2/cases/"


def test_testy_api_errors_are_tool_errors_not_protocol_errors():
    class ErrorGateway(FakeGateway):
        def request(self, method, path, *, query=None, body=None):
            return Response(403, {"detail": "forbidden"})

    response = MCPProtocol().handle(
        request(
            "tools/call",
            {"name": "testy_get", "arguments": {"entity": "projects", "id": 9}},
        ),
        ErrorGateway(),
    )

    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["details"]["status"] == 403


def test_notification_has_no_response():
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}

    assert MCPProtocol().handle(notification, FakeGateway()) is None


def test_batch_is_rejected_for_current_protocol_versions():
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("ping", request_id="ping-1"),
    ]

    response = MCPProtocol().handle(batch, FakeGateway())

    assert response["error"]["code"] == -32600
    assert "batches" in response["error"]["message"]


def test_batch_is_supported_for_2025_03_26():
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("ping", request_id="ping-1"),
    ]

    response = MCPProtocol().handle(batch, FakeGateway(), protocol_version="2025-03-26")

    assert response == [{"jsonrpc": "2.0", "id": "ping-1", "result": {}}]


def test_unknown_tool_is_a_protocol_error():
    response = MCPProtocol().handle(
        request("tools/call", {"name": "missing", "arguments": {}}),
        FakeGateway(),
    )

    assert response["error"]["code"] == -32602


def test_modern_request_requires_metadata():
    response = MCPProtocol().handle(
        request("ping"),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert response["error"]["code"] == -32602


def test_unknown_method_returns_jsonrpc_error():
    response = MCPProtocol().handle(request("unknown/method"), FakeGateway())

    assert response["error"]["code"] == -32601


def test_modern_ping_is_method_not_found():
    response = MCPProtocol().handle(
        request("ping", modern_params()),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert response["error"]["code"] == -32601


def test_modern_initialize_is_method_not_found():
    response = MCPProtocol().handle(
        request("initialize", modern_params(protocolVersion="2025-11-25")),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert response["error"]["code"] == -32601


def test_legacy_ping_still_answers():
    response = MCPProtocol().handle(request("ping"), FakeGateway())

    assert response["result"] == {}


def test_http_status_mapping_follows_transport_rules():
    modern = MODERN_PROTOCOL_VERSION
    legacy = LATEST_LEGACY_PROTOCOL_VERSION

    assert MCPProtocol.http_status_for({"error": {"code": -32601}}, modern) == 404
    assert MCPProtocol.http_status_for({"error": {"code": -32602}}, modern) == 400
    assert MCPProtocol.http_status_for({"error": {"code": -32603}}, modern) == 200
    assert MCPProtocol.http_status_for({"error": {"code": -32602}}, legacy) == 200
    assert MCPProtocol.http_status_for({"error": {"code": -32600}}, None) == 400
    assert MCPProtocol.http_status_for({"result": {"resultType": "complete"}}, modern) == 200
    assert MCPProtocol.http_status_for([{"jsonrpc": "2.0", "id": 1, "result": {}}], legacy) == 200


def test_modern_missing_client_capabilities_is_invalid_params():
    params = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
        }
    }
    response = MCPProtocol().handle(
        request("tools/list", params),
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert response["error"]["code"] == -32602


def test_non_object_params_is_invalid_params():
    response = MCPProtocol().handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []},
        FakeGateway(),
    )

    assert response["error"]["code"] == -32602


def test_malformed_notification_is_rejected_not_accepted():
    from testy_mcp.protocol import RejectedNotification

    response = MCPProtocol().handle({"method": "notifications/initialized"}, FakeGateway())

    assert isinstance(response, RejectedNotification)
    assert response.response["error"]["code"] == -32600
    assert "id" not in response.response


def test_request_method_sent_as_notification_is_rejected():
    from testy_mcp.protocol import RejectedNotification

    response = MCPProtocol().handle(
        {"jsonrpc": "2.0", "method": "tools/list"}, FakeGateway()
    )

    assert isinstance(response, RejectedNotification)
    assert response.response["error"]["code"] == -32600


def test_removed_notification_is_rejected_for_modern_requests():
    from testy_mcp.protocol import RejectedNotification

    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": modern_params(),
    }

    response = MCPProtocol().handle(
        notification,
        FakeGateway(),
        protocol_version=MODERN_PROTOCOL_VERSION,
    )

    assert isinstance(response, RejectedNotification)
    assert response.response["error"]["code"] == -32601


def test_batch_drops_notification_rejections():
    batch = [
        {"jsonrpc": "2.0", "method": "tools/list"},
        request("ping", request_id="ping-1"),
    ]

    response = MCPProtocol().handle(batch, FakeGateway(), protocol_version="2025-03-26")

    assert response == [{"jsonrpc": "2.0", "id": "ping-1", "result": {}}]
