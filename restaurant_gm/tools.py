"""MongoDB MCP toolsets for the Restaurant GM agents.

Each agent gets its OWN ``McpToolset`` with a scoped ``tool_filter`` so it can only
invoke the MongoDB operations it actually needs. This is the agents' single data
path (invariant #4: agents reach Mongo through the MongoDB MCP server; deterministic
plumbing uses the plain driver instead).

All toolsets talk to the same Atlas cluster and differ only in the operations they
expose. ``tool_filter`` scopes *operations*, not collections — collection-level
scope is enforced by each agent's instruction and, in production, by a
least-privilege MongoDB user. (Exact tool_filter + collection scope for every agent
is documented at the top of that agent's YAML in this folder.)

HOW TO EXTEND (for collaborators):
``inventory_toolset`` below is the fully worked-out example. To wire up another
agent, copy that one block and change only the ``tool_filter`` to match the values
in its YAML. Stubs for the remaining four are at the bottom — uncomment and go.
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
    return StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["-y", "mongodb-mcp-server"],
            env={"MDB_MCP_CONNECTION_STRING": connection_string},
        ),
    )


# --- EXEMPLAR -----------------------------------------------------------------
# Inventory monitors stock, derives the 86 list, and reorders. It needs reads
# (find / aggregate) plus two writes (insert purchase_orders, update live_metrics).
# Referenced from inventory_agent.yaml as `restaurant_gm.tools.inventory_toolset`.
#   read:  raw_ingredients, recipes, vendors, orders
#   write: purchase_orders, live_metrics, agent_events
inventory_toolset = McpToolset(
    connection_params=_mongodb_mcp_connection(),
    tool_filter=["find", "aggregate", "insert-many", "update-many"],
)


# --- TODO (collaborators): define the rest the same way -----------------------
# Copy the exemplar above; only the tool_filter changes. Each list mirrors the
# `tool_filter:` documented in that agent's YAML.
#
# order_mgmt_toolset = McpToolset(
#     connection_params=_mongodb_mcp_connection(),
#     tool_filter=["find", "aggregate", "update-many", "insert-many"],
# )
#
# billing_toolset = McpToolset(
#     connection_params=_mongodb_mcp_connection(),
#     tool_filter=["find", "aggregate", "insert-many", "update-many"],
# )
#
# outreach_toolset = McpToolset(
#     connection_params=_mongodb_mcp_connection(),
#     tool_filter=["find", "insert-many"],
# )
#
# central_toolset = McpToolset(
#     connection_params=_mongodb_mcp_connection(),
#     tool_filter=["find", "update-many", "insert-many"],
# )
