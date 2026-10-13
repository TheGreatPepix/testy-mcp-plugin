from pathlib import Path

import pytest

import testy_mcp


def test_plugin_catalog_links_to_token_management_page():
    plugin_config = testy_mcp.config()

    assert plugin_config.index_reverse_name == "tokens"
    assert plugin_config.version == "1.0.1"


def test_token_page_documents_agent_connections():
    template = (
        Path(__file__).parents[1]
        / "testy_mcp"
        / "templates"
        / "testy_mcp"
        / "tokens.html"
    ).read_text(encoding="utf-8")

    for client in (
        "Claude Code",
        "Claude Desktop",
        "Codex",
        "Cursor",
        "VS Code",
        "Gemini CLI",
        "mcp-remote",
    ):
        assert client in template, client

    assert "Authorization: Token &lt;ваш-токен&gt;" in template
    assert "Authorization: Bearer" not in template
    assert '"Bearer ' not in template
    assert "TESTY_MCP_TOKEN" in template
    assert '"type": "http"' in template
    assert "env_http_headers" in template
    assert "httpUrl" in template


def test_token_page_leaves_usage_rules_to_the_server():
    template = (
        Path(__file__).parents[1]
        / "testy_mcp"
        / "templates"
        / "testy_mcp"
        / "tokens.html"
    ).read_text(encoding="utf-8")

    assert "Экономия токенов" not in template
    assert "описаниях" in template


def test_tokens_template_parses_as_a_django_template():
    pytest.importorskip("django")
    from django.template import Engine

    template_path = (
        Path(__file__).parents[1]
        / "testy_mcp"
        / "templates"
        / "testy_mcp"
        / "tokens.html"
    )

    Engine().from_string(template_path.read_text(encoding="utf-8"))


def test_endpoint_answers_with_and_without_trailing_slash():
    import ast

    source = (Path(__file__).parents[1] / "testy_mcp" / "urls.py").read_text(encoding="utf-8")
    routes = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "path":
            route, view = node.args[0].value, ast.unparse(node.args[1])
            routes[route] = view

    assert testy_mcp.config().plugin_base_url == ""
    assert routes["mcp"] == routes["mcp/"] == "MCPView.as_view()"
    assert "mcp/tokens/" in routes
