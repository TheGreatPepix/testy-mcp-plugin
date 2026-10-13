# ruff: noqa: E402

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework")

from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="test-key",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["testserver", "testy.example.com"],
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rest_framework"],
        REST_FRAMEWORK={},
    )

import django

django.setup()

from django.urls import path
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from testy_mcp.gateway import GatewayError, TestyAPIGateway as APIGateway


class EchoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "username": request.user.username,
                "labels": request.query_params.getlist("labels"),
                "is_archive": request.query_params.get("is_archive"),
                "host": request.get_host(),
                "scheme": request.scheme,
            },
        )


urlpatterns = [path("api/v2/echo/", EchoView.as_view())]


class User:
    username = "agent"
    is_authenticated = True


@pytest.mark.parametrize(
    "path, expected",
    [
        ("api/v2/projects/", "/api/v2/projects/"),
        ("/api/v2/cases/42/", "/api/v2/cases/42/"),
        ("/api/v2/testplans/9/statistics/", "/api/v2/testplans/9/statistics/"),
    ],
)
def test_validate_path_accepts_api_v2(path, expected):
    assert APIGateway._validate_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/",
        "/admin/",
        "/api/v2/../token/",
        "/api/v2/projects/?page=1",
        "/api/v2/projects/%2e%2e/token/",
    ],
)
def test_validate_path_rejects_unsafe_paths(path):
    with pytest.raises(GatewayError):
        APIGateway._validate_path(path)


def test_encode_query_handles_lists_booleans_and_json():
    encoded = APIGateway._encode_query(
        {
            "labels": [1, 2],
            "is_archive": False,
            "filter_conditions": {"search": "smoke"},
            "skip": None,
        },
    )

    assert "labels=1" in encoded
    assert "labels=2" in encoded
    assert "is_archive=false" in encoded
    assert "filter_conditions=" in encoded
    assert "skip=" not in encoded


def test_request_dispatches_to_drf_with_user_and_query():
    response = APIGateway(User()).request(
        "GET",
        "/api/v2/echo/",
        query={"labels": [1, 2], "is_archive": False},
    )

    assert response.status == 200
    assert response.data == {
        "username": "agent",
        "labels": ["1", "2"],
        "is_archive": "false",
        "host": "testserver",
        "scheme": "http",
    }


def test_request_forwards_public_host_and_scheme():
    response = APIGateway(User(), host="testy.example.com", secure=True).request(
        "GET",
        "/api/v2/echo/",
    )

    assert response.status == 200
    assert response.data["host"] == "testy.example.com"
    assert response.data["scheme"] == "https"


def test_response_size_check_can_be_skipped_for_internal_calls():
    settings.TESTY_MCP_MAX_RESPONSE_BYTES = 10
    try:
        with pytest.raises(GatewayError, match="too large"):
            APIGateway(User()).request("GET", "/api/v2/echo/")

        response = APIGateway(User()).request("GET", "/api/v2/echo/", check_size=False)
    finally:
        del settings.TESTY_MCP_MAX_RESPONSE_BYTES

    assert response.status == 200
