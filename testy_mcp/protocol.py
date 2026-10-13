import logging
from dataclasses import dataclass
from typing import Any

from testy_mcp import __version__
from testy_mcp.tools import (
    TOOLS,
    TOOLS_BY_NAME,
    AI_COPIED,
    AI_EDITED,
    AI_GENERATED,
    ToolError,
    ai_label,
    call_tool,
    tool_error_result,
)

JSONRPC_VERSION = "2.0"
MODERN_PROTOCOL_VERSION = "2026-07-28"
LATEST_LEGACY_PROTOCOL_VERSION = "2025-11-25"
LATEST_PROTOCOL_VERSION = MODERN_PROTOCOL_VERSION
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {
        MODERN_PROTOCOL_VERSION,
        LATEST_LEGACY_PROTOCOL_VERSION,
        "2025-06-18",
        "2025-03-26",
    },
)

LEGACY_ONLY_METHODS = frozenset(
    {"initialize", "ping", "notifications/initialized"}
)

BAD_REQUEST_ERROR_CODES = frozenset({-32700, -32600, -32602})

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JSONRPCError(Exception):
    code: int
    message: str
    data: Any = None


@dataclass(slots=True)
class RejectedNotification:
    """A notification the server cannot accept; HTTP must answer with an error."""

    response: dict[str, Any]


class MCPProtocol:
    def handle(
        self, payload: Any, gateway, *, protocol_version: str | None = None
    ) -> Any:
        if isinstance(payload, list):
            if protocol_version != "2025-03-26":
                return self._error(
                    None, -32600, "Invalid Request: JSON-RPC batches are not supported"
                )
            if not payload:
                return self._error(None, -32600, "Invalid Request")
            responses = [
                self._handle_message(
                    message, gateway, protocol_version=protocol_version
                )
                for message in payload
            ]
            return [
                response
                for response in responses
                if response is not None
                and not isinstance(response, RejectedNotification)
            ] or None
        return self._handle_message(payload, gateway, protocol_version=protocol_version)

    def _handle_message(
        self,
        message: Any,
        gateway,
        *,
        protocol_version: str | None,
    ) -> dict[str, Any] | None:
        request_id = message.get("id") if isinstance(message, dict) else None
        try:
            if (
                not isinstance(message, dict)
                or message.get("jsonrpc") != JSONRPC_VERSION
            ):
                raise JSONRPCError(-32600, "Invalid Request")
            if "id" in message and not self._valid_request_id(message["id"]):
                raise JSONRPCError(
                    -32600, "Invalid Request: id must be a string or integer"
                )
            method = message.get("method")
            if not isinstance(method, str):
                raise JSONRPCError(-32600, "Invalid Request")
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise JSONRPCError(-32602, "Invalid params")

            is_notification = "id" not in message
            if is_notification and not method.startswith("notifications/"):
                raise JSONRPCError(-32600, "Requests must include an id")
            request_version = self._request_protocol_version(params, protocol_version)
            if (
                method == "server/discover"
                and request_version != MODERN_PROTOCOL_VERSION
            ):
                raise JSONRPCError(
                    -32602,
                    "server/discover requires the 2026-07-28 per-request metadata",
                )
            if request_version == MODERN_PROTOCOL_VERSION:
                self._validate_modern_metadata(params)
            result = self._dispatch(
                method,
                params,
                gateway,
                is_notification=is_notification,
                protocol_version=request_version,
            )
            if is_notification:
                return None
            if request_version == MODERN_PROTOCOL_VERSION:
                result = self._modern_result(result)
            return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}
        except JSONRPCError as exc:
            if isinstance(message, dict) and "id" not in message:
                return self._rejected_notification(exc)
            return self._error(request_id, exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("Unhandled MCP request error")
            if isinstance(message, dict) and "id" not in message:
                return self._rejected_notification(
                    JSONRPCError(-32603, "Internal error")
                )
            return self._error(request_id, -32603, "Internal error")

    def _dispatch(
        self,
        method: str,
        params: dict[str, Any],
        gateway,
        *,
        is_notification: bool,
        protocol_version: str | None,
    ) -> Any:
        if (
            protocol_version == MODERN_PROTOCOL_VERSION
            and method in LEGACY_ONLY_METHODS
        ):
            raise JSONRPCError(-32601, "Method not found")
        if method == "server/discover":
            return {
                "supportedVersions": [MODERN_PROTOCOL_VERSION],
                "capabilities": {"tools": {"listChanged": False}},
                "instructions": self._instructions(),
                "ttlMs": 3600000,
                "cacheScope": "private",
            }
        if method == "initialize":
            requested = params.get("protocolVersion")
            legacy_versions = SUPPORTED_PROTOCOL_VERSIONS - {MODERN_PROTOCOL_VERSION}
            negotiated_version = (
                requested
                if requested in legacy_versions
                else LATEST_LEGACY_PROTOCOL_VERSION
            )
            return {
                "protocolVersion": negotiated_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": self._server_info(),
                "instructions": self._instructions(),
            }
        if method == "ping":
            return {}
        if method == "notifications/initialized" and is_notification:
            return None
        if method == "tools/list":
            cursor = params.get("cursor")
            if cursor is not None:
                if not isinstance(cursor, str):
                    raise JSONRPCError(-32602, "tools/list cursor must be a string")
                raise JSONRPCError(-32602, "Unknown tools/list cursor")
            result = {"tools": [tool.as_mcp() for tool in TOOLS]}
            if protocol_version == MODERN_PROTOCOL_VERSION:
                result.update({"ttlMs": 3600000, "cacheScope": "private"})
            return result
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise JSONRPCError(-32602, "tools/call requires a tool name")
            if name not in TOOLS_BY_NAME:
                raise JSONRPCError(-32602, f"Unknown tool: {name}")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise JSONRPCError(-32602, "tools/call arguments must be an object")
            try:
                return call_tool(name, arguments, gateway)
            except ToolError as exc:
                return tool_error_result(exc)
        raise JSONRPCError(-32601, "Method not found")

    @staticmethod
    def _request_protocol_version(
        params: dict[str, Any], header_version: str | None
    ) -> str | None:
        metadata = params.get("_meta", {})
        if isinstance(metadata, dict):
            body_version = metadata.get("io.modelcontextprotocol/protocolVersion")
            if isinstance(body_version, str):
                return body_version
        return header_version

    @staticmethod
    def _validate_modern_metadata(params: dict[str, Any]) -> None:
        metadata = params.get("_meta")
        if not isinstance(metadata, dict):
            raise JSONRPCError(-32602, "Modern MCP requests require params._meta")
        if (
            metadata.get("io.modelcontextprotocol/protocolVersion")
            != MODERN_PROTOCOL_VERSION
        ):
            raise JSONRPCError(
                -32602, "Invalid or missing MCP protocol version metadata"
            )
        capabilities = metadata.get("io.modelcontextprotocol/clientCapabilities")
        if not isinstance(capabilities, dict):
            raise JSONRPCError(
                -32602, "Invalid or missing MCP client capabilities metadata"
            )
        client_info = metadata.get("io.modelcontextprotocol/clientInfo")
        if client_info is not None:
            if not isinstance(client_info, dict):
                raise JSONRPCError(-32602, "Invalid MCP client info metadata")
            if not isinstance(client_info.get("name"), str) or not isinstance(
                client_info.get("version"), str
            ):
                raise JSONRPCError(
                    -32602, "MCP client info requires string name and version"
                )

    @classmethod
    def _modern_result(cls, result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        modern = dict(result)
        modern.setdefault("resultType", "complete")
        metadata = modern.setdefault("_meta", {})
        if isinstance(metadata, dict):
            metadata.setdefault(
                "io.modelcontextprotocol/serverInfo", cls._server_info()
            )
        return modern

    @staticmethod
    def _server_info() -> dict[str, str]:
        return {"name": "testy-mcp", "version": __version__}

    @staticmethod
    def _valid_request_id(value: Any) -> bool:
        return isinstance(value, (str, int)) and not isinstance(value, bool)

    @staticmethod
    def _instructions() -> str:
        actions = {AI_GENERATED: "created", AI_EDITED: "edited", AI_COPIED: "copied"}
        marked = [
            f"{action} -> '{marker['name']}'"
            for kind, action in actions.items()
            if (marker := ai_label(kind)) is not None
        ]
        label_rule = (
            "Every test case you touch is labeled automatically ("
            + ", ".join(marked)
            + "); the labels are kept on later updates. Do not add, rename or remove them "
            "yourself. "
            if marked
            else ""
        )
        return (
            "TestY test management server. Data model: project -> suites (folder tree) -> cases "
            "(test case definitions); project -> test_plans (tree) -> tests (a case inside a plan) "
            "-> results (one execution with a status). "
            "Resolve ids first: find the project with testy_list(entity='projects', "
            "query={'search': '<name>'}), then the suite with query={'project': <id>, 'search': "
            "'<name>'}; reuse known ids and never dump whole collections. "
            "Before creating a case, search the target suite for a similar name and update the "
            "existing case instead of duplicating it. Read only what you edit: testy_get with "
            "fields, e.g. ['id','name','steps']; write replies are already a compact summary, so "
            "do not re-read to confirm a write. Write cases in the language of the project's "
            "existing cases; for steps use data={'project': <id>, 'suite': <id>, 'name': '...', "
            "'steps': [['action', 'expected'], ...]}. "
            + label_rule
            + "On an error, details.data carries TestY's validation messages per field: fix the "
            "request instead of guessing. STRICT RULE: never delete anything without the user's "
            "explicit confirmation in this conversation; name exactly what will be deleted, wait "
            "for a clear yes, and only then pass confirmed=true. Confirm bulk changes with the "
            "user too. "
            "Use testy_api_request only for API v2 routes the typed tools do not cover."
        )

    @classmethod
    def _rejected_notification(cls, exc: JSONRPCError) -> RejectedNotification:
        response = cls._error(None, exc.code, exc.message, exc.data)
        response.pop("id", None)
        return RejectedNotification(response)

    @staticmethod
    def _error(
        request_id: Any, code: int, message: str, data: Any = None
    ) -> dict[str, Any]:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}

    @staticmethod
    def http_status_for(response: Any, protocol_version: str | None) -> int:
        """Map a JSON-RPC error to the HTTP status required by the transport.

        Modern (2026-07-28) requests must answer malformed requests and unknown
        methods with 400 and 404 respectively. Header/version errors never reach
        this helper because the transport raises them before dispatch.
        """

        error = response.get("error") if isinstance(response, dict) else None
        if not isinstance(error, dict):
            return 200
        code = error.get("code")
        if code == -32601 and protocol_version == MODERN_PROTOCOL_VERSION:
            return 404
        if (
            code in BAD_REQUEST_ERROR_CODES
            and protocol_version == MODERN_PROTOCOL_VERSION
        ):
            return 400
        if code == -32600:
            return 400
        return 200
