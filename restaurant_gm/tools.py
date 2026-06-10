"""MongoDB MCP toolsets for the Restaurant GM agents.

Single shared McpToolset — one npx process, one Atlas connection. Per-agent
operation scoping (tool_filter) was causing 5 zombie npx processes per session
because ADK never cleans up module-level McpToolset subprocess on session end.
Collection-level scope is still enforced by each agent's instruction.

Real tool names verified by running the server and calling get_tools() (2026-06-09):
    aggregate, aggregate-db, collection-indexes, collection-schema,
    collection-storage-size, connect, count, create-collection, create-index,
    db-stats, delete-many, drop-collection, drop-database, drop-index, explain,
    export, find, insert-many, list-collections, list-databases, mongodb-logs,
    rename-collection, update-many
Note: there is no insert-one or update-one — bulk operations only.
"""

import os

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters


def _mongodb_mcp_connection() -> StdioConnectionParams:
    """Stdio connection to the official MongoDB MCP server (run via ``npx`` / Node).

    Reads the Atlas connection string from ``MONGODB_CONNECTION_STRING`` (see
    ``.env`` locally / Secret Manager when deployed) and hands it to the server
    through its ``MDB_MCP_CONNECTION_STRING`` env var. Because the server runs under
    Node, any deploy container needs Node installed alongside Python.
    """
    connection_string = os.environ["MONGODB_CONNECTION_STRING"]
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")

    # Inject the database name into the connection string so the MCP server
    # queries the right database. Atlas URIs look like:
    #   mongodb+srv://user:pass@cluster.net/?params
    # We need:
    #   mongodb+srv://user:pass@cluster.net/restaurant_gm?params
    if "?" in connection_string:
        host, params = connection_string.split("?", 1)
        host = host.rstrip("/")
        connection_string_with_db = f"{host}/{db_name}?{params}"
    else:
        connection_string_with_db = f"{connection_string.rstrip('/')}/{db_name}"

    return StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["-y", "mongodb-mcp-server"],
            env={"MDB_MCP_CONNECTION_STRING": connection_string_with_db},
        ),
    )


# Single shared toolset — all agents reference one of these aliases.
# Scoping is enforced by each agent's instruction, not tool_filter.
# Full op set: find + aggregate for reads, insert-many + update-many for writes,
# count for quick checks.
_shared = McpToolset(
    connection_params=_mongodb_mcp_connection(),
    tool_filter=["find", "aggregate", "count", "insert-many", "update-many"],
)

# Aliases so the YAML `tools: - name:` references still resolve.
order_mgmt_toolset = _shared
inventory_toolset = _shared
billing_toolset = _shared
outreach_toolset = _shared
central_toolset = _shared
