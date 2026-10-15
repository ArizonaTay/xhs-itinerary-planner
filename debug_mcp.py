"""Debug script to test MCP Streamable HTTP connection to xiaohongshu-mcp."""
import asyncio
import json
import logging
import os

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp-debug")

from dotenv import load_dotenv
load_dotenv()

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    xhs_mcp_url = os.getenv("XHS_MCP_URL", "http://localhost:18060/mcp")
    xhs_cookie = os.getenv("XHS_COOKIE", "")

    logger.info("XHS_COOKIE set: %s", "YES" if xhs_cookie else "NO")
    logger.info("XHS_MCP_URL: %s", xhs_mcp_url)

    if not xhs_cookie:
        logger.error("XHS_COOKIE is not set!")
        return

    try:
        logger.info("Connecting to MCP server via Streamable HTTP...")
        async with streamable_http_client(xhs_mcp_url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                logger.info("Initializing session...")
                init_result = await session.initialize()
                logger.info("Session initialized: server=%s version=%s",
                            init_result.serverInfo.name, init_result.serverInfo.version)

                # Step 1: List available tools
                logger.info("Listing available tools...")
                tools_result = await session.list_tools()
                logger.info("Found %d tools:", len(tools_result.tools))
                for tool in tools_result.tools:
                    logger.info("  - %s: %s", tool.name, tool.description)
                    logger.info("    inputSchema: %s", json.dumps(tool.inputSchema, indent=4))

                # Step 2: Call search_feeds
                logger.info("\nCalling search_feeds tool...")
                try:
                    result = await asyncio.wait_for(
                        session.call_tool(
                            "search_feeds",
                            arguments={
                                "keyword": "Tokyo travel",
                                "filters": {"sort_by": "最新", "note_type": "图文"},
                            },
                        ),
                        timeout=60.0,
                    )
                    logger.info("CallToolResult received:")
                    logger.info("  isError: %s", result.isError)
                    logger.info("  content items: %d", len(result.content))
                    for i, block in enumerate(result.content):
                        text = block.text[:500] if hasattr(block, 'text') and block.text else "NONE"
                        logger.info("  content[%d] type=%s text_preview=%s",
                                    i, type(block).__name__, text)
                except asyncio.TimeoutError:
                    logger.error("TIMEOUT: search_feeds did not respond within 60 seconds")
                except Exception as e:
                    logger.error("search_feeds failed: %s", e)
                    import traceback
                    traceback.print_exc()

    except Exception as e:
        logger.error("Connection failed: %s", e)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
