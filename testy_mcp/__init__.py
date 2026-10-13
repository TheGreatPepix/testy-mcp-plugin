__version__ = "1.0.1"
default_app_config = "testy_mcp.apps.TestyMCPConfig"

try:
    from testy.plugins.hooks import TestyPluginConfig, hookimpl
except ImportError:  # pragma: no cover
    class TestyPluginConfig:
        pass

    def hookimpl(func):
        return func


class TestyMCPPluginConfig(TestyPluginConfig):
    package_name = "testy_mcp"
    verbose_name = "MCP Server"
    description = "Expose the TestY API to AI agents through Model Context Protocol."
    version = __version__
    plugin_base_url = ""
    author = "id.safronov"
    index_reverse_name = "tokens"
    urls_module = "testy_mcp.urls"
    min_version = "2.2.1"


@hookimpl
def config():
    return TestyMCPPluginConfig
