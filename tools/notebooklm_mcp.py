#!/usr/bin/env python3
"""NotebookLM MCP Server - Browser-based integration for job research"""

import os
import json
from mcp.server import Server
from mcp.types import Tool, TextContent, Resource
from mcp.server.stdio import stdio_server

app = Server("notebooklm")

# Configuration
NOTEBOOKLM_URL = os.environ.get("NOTEBOOKLM_URL", "https://notebooklm.google.com")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available NotebookLM tools."""
    return [
        Tool(
            name="notebooklm_open",
            description="Open NotebookLM in browser for manual interaction",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Notebook ID to open"}
                }
            }
        ),
        Tool(
            name="notebooklm_get_url",
            description="Get the NotebookLM URL for browser navigation",
            inputSchema={"type": "object", "properties": {}}
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute NotebookLM tools."""
    
    if name == "notebooklm_open":
        notebook_id = arguments.get("notebook_id", "")
        if notebook_id:
            url = f"{NOTEBOOKLM_URL}/_edit/{notebook_id}"
        else:
            url = NOTEBOOKLM_URL
        return [TextContent(
            type="text",
            text=f"NotebookLM URL: {url}\n\nUse Antigravity's Browser Agent to navigate to this URL."
        )]
    
    elif name == "notebooklm_get_url":
        return [TextContent(
            type="text",
            text=f"NotebookLM URL: {NOTEBOOKLM_URL}\n\nUse Antigravity's Browser Agent to access NotebookLM."
        )]
    
    return [TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
