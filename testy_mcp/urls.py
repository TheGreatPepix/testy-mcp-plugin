from django.contrib.auth.decorators import login_required
from django.urls import path

from testy_mcp.views import GenerateTokenView, MCPView, RevokeTokenView, TokensView

urlpatterns = [
    path("mcp", MCPView.as_view(), name="endpoint-no-slash"),
    path("mcp/", MCPView.as_view(), name="endpoint"),
    path("mcp/tokens/", login_required(TokensView.as_view()), name="tokens"),
    path(
        "mcp/tokens/generate/",
        login_required(GenerateTokenView.as_view()),
        name="generate-token",
    ),
    path("mcp/tokens/revoke/", login_required(RevokeTokenView.as_view()), name="revoke-token"),
]
