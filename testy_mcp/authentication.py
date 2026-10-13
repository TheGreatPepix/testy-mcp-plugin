from rest_framework.authentication import get_authorization_header

from testy.root.auth.auth_classes import TokenAuthenticationTTL


class MCPBearerTokenAuthentication(TokenAuthenticationTTL):
    """Accept TestY TTL tokens in the Bearer scheme used by MCP clients.

    JWTs are left to TestY's regular JWT authentication class. A JWT always
    contains three dot-separated segments, while TestY TTL tokens are opaque
    hexadecimal keys.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if len(auth) == 2 and auth[0].lower() == b"bearer" and auth[1].count(b".") == 2:
            return None
        return super().authenticate(request)
