import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from testy.root.auth.models import TTLToken

from testy_mcp.authentication import MCPBearerTokenAuthentication
from testy_mcp.errors import RATE_LIMIT_EXCEEDED_ERROR
from testy_mcp.gateway import TestyAPIGateway
from testy_mcp.protocol import MCPProtocol, RejectedNotification
from testy_mcp.rate_limit import check_rate_limit
from testy_mcp.transport import (
    TransportError,
    batch_uses_modern_metadata,
    error_payload,
    tool_call_messages,
    validate_origin,
    validate_request_headers,
)

MCP_TOKEN_DESCRIPTION_PREFIX = "MCP agent (testy_mcp)"
logger = logging.getLogger(__name__)


def _tokens_url() -> str:
    return reverse("plugins:testy_mcp:tokens")


class TokensView(View):
    template_name = "testy_mcp/tokens.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        tokens = TTLToken.objects.filter(
            user=request.user,
            description__startswith=MCP_TOKEN_DESCRIPTION_PREFIX,
        ).order_by("-created")
        token_rows = [
            {
                "key": token.key,
                "key_prefix": token.key[:8],
                "label": (token.description or "").removeprefix(
                    f"{MCP_TOKEN_DESCRIPTION_PREFIX}: "
                ),
                "created": token.created,
                "expiration_date": token.expiration_date,
            }
            for token in tokens
        ]
        return render(
            request,
            self.template_name,
            {
                "tokens": token_rows,
                "new_token": request.session.pop("testy_mcp_new_token", None),
                "new_token_expires": request.session.pop(
                    "testy_mcp_new_token_expires", None
                ),
                "mcp_endpoint": request.build_absolute_uri(
                    reverse("plugins:testy_mcp:endpoint")
                ),
            },
        )


class GenerateTokenView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        label = request.POST.get("description", "").strip() or "Codex agent"
        description_field = TTLToken._meta.get_field("description")
        max_label_length = max(
            1,
            description_field.max_length - len(MCP_TOKEN_DESCRIPTION_PREFIX) - 2,
        )
        if len(label) > max_label_length:
            messages.error(
                request,
                f"Название токена не должно превышать {max_label_length} символов.",
            )
            return redirect(_tokens_url())

        token = TTLToken.objects.create(
            user=request.user,
            description=f"{MCP_TOKEN_DESCRIPTION_PREFIX}: {label}",
        )
        request.session["testy_mcp_new_token"] = token.key
        request.session["testy_mcp_new_token_expires"] = (
            token.expiration_date.isoformat()
        )
        messages.success(
            request,
            "MCP-токен создан. Скопируйте его сейчас: повторно он целиком не показывается.",
        )
        return redirect(_tokens_url())


class RevokeTokenView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        token = get_object_or_404(
            TTLToken,
            key=request.POST.get("key", ""),
            user=request.user,
            description__startswith=MCP_TOKEN_DESCRIPTION_PREFIX,
        )
        token.delete()
        messages.success(request, "MCP-токен отозван.")
        return redirect(_tokens_url())


class MCPView(APIView):
    """Stateless MCP Streamable HTTP endpoint using JSON responses."""

    permission_classes = [IsAuthenticated]
    authentication_classes = [
        MCPBearerTokenAuthentication,
        *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
    ]
    renderer_classes = [JSONRenderer]
    protocol = MCPProtocol()

    def dispatch(self, request, *args, **kwargs):
        try:
            validate_origin(
                request.headers.get("Origin"),
                f"{request.scheme}://{request.get_host()}",
                getattr(settings, "TESTY_MCP_ALLOWED_ORIGINS", ()),
            )
        except TransportError as exc:
            return JsonResponse(
                error_payload(None, exc),
                status=exc.http_status,
                json_dumps_params={"ensure_ascii": False},
            )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        try:
            payload = request.data
        except ParseError:
            payload = None
        if not isinstance(payload, (dict, list)):
            return Response(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_id = payload.get("id") if isinstance(payload, dict) else None
        if isinstance(payload, list):
            batch_version = request.headers.get("MCP-Protocol-Version") or "2025-03-26"
            if batch_version != "2025-03-26" or batch_uses_modern_metadata(payload):
                return Response(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "Invalid Request: JSON-RPC batches are not supported",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            protocol_version = batch_version
        else:
            try:
                protocol_version = validate_request_headers(payload, request.headers)
            except TransportError as exc:
                return Response(error_payload(request_id, exc), status=exc.http_status)

        for tool_call in tool_call_messages(payload):
            rate = check_rate_limit(
                cache,
                str(request.user.pk),
                limit=getattr(settings, "TESTY_MCP_RATE_LIMIT_CALLS", 120),
                window_seconds=getattr(
                    settings, "TESTY_MCP_RATE_LIMIT_WINDOW_SECONDS", 60
                ),
            )
            if not rate.allowed:
                error = TransportError(
                    RATE_LIMIT_EXCEEDED_ERROR,
                    "Tool call rate limit exceeded",
                    http_status=429,
                )
                return Response(
                    error_payload(request_id, error),
                    status=error.http_status,
                    headers={"Retry-After": str(rate.retry_after)},
                )
            call_params = tool_call.get("params")
            logger.info(
                "MCP tool call user=%s tool=%s",
                request.user.pk,
                call_params.get("name") if isinstance(call_params, dict) else None,
            )

        gateway = TestyAPIGateway(
            request.user,
            request.auth,
            host=request.get_host(),
            secure=request.is_secure(),
        )
        result = self.protocol.handle(
            payload,
            gateway,
            protocol_version=protocol_version,
        )
        if isinstance(result, RejectedNotification):
            return Response(result.response, status=status.HTTP_400_BAD_REQUEST)
        if result is None:
            return Response(status=status.HTTP_202_ACCEPTED)
        response_status = self.protocol.http_status_for(result, protocol_version)
        return Response(result, status=response_status)

    def get(self, request):
        return Response(
            {
                "detail": "This stateless MCP endpoint accepts JSON-RPC messages over POST.",
                "endpoint": request.build_absolute_uri(),
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": "POST"},
        )
