#!/usr/bin/env python3
"""
TradingView MCP Server - WORKING VERSION
Uses Playwright for lightweight browser automation (required because TradingView renders charts client-side).
Optimized with browser reuse and cookie-based authentication for minimal resource usage.
"""

import os
import base64
import logging
import asyncio
import json
import tempfile
from typing import Optional, List, Dict, Any
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext
import MetaTrader5 as mt5
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent
from mcp.server.stdio import stdio_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Global client instance
_client: Optional["TradingViewClient"] = None


class TradingViewClient:
    """
    Client for TradingView and MetaTrader 5 charting operations.
    Encapsulates browser automation and data fetching.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        session_id_sign: Optional[str] = None,
        mt5_config: Optional[Dict[str, Any]] = None,
    ):
        self.session_id = session_id or os.getenv("TRADINGVIEW_SESSION_ID")
        self.session_id_sign = session_id_sign or os.getenv("TRADINGVIEW_SESSION_ID_SIGN")
        self.mt5_config = mt5_config or {
            "login": os.getenv("MT5_LOGIN"),
            "password": os.getenv("MT5_PASSWORD"),
            "server": os.getenv("MT5_SERVER"),
            "path": os.getenv("MT5_PATH"),
        }

        self._playwright = None
        self._browser = None
        self._context = None

    async def _get_context(self) -> BrowserContext:
        """Get or create the browser context."""
        if self._context:
            return self._context

        if not self.session_id or not self.session_id_sign:
            raise ValueError("TradingView credentials missing")

        if not self._playwright:
            self._playwright = await async_playwright().start()

        assert self._playwright is not None
        if not self._browser:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ],
            )
            logger.info("Browser launched successfully")

        assert self._browser is not None
        if self._browser is None:
            raise RuntimeError("Browser failed to launch")

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        if self._context is None:
            raise RuntimeError("Failed to create browser context")

        assert self._context is not None
        await self._context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": self.session_id,
                    "domain": ".tradingview.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "sessionid_sign",
                    "value": self.session_id_sign,
                    "domain": ".tradingview.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "g_state",
                    "value": '{"i_l":1,"i_ll":1773910627140,"i_e":{"enable_itp_optimization":0}}',
                    "domain": ".tradingview.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                },
            ]
        )
        logger.info("Browser context created with authentication")
        return self._context

    async def _dismiss_popup(self, page, width: int = 1200, height: int = 600) -> None:
        """Attempt to dismiss any promotional or modal popup on TradingView."""

        # Strategy 1: Escape key
        try:
            await page.keyboard.press("Escape")
            logger.info("Sent Escape key")
            await asyncio.sleep(0.8)
        except Exception:
            pass

        # Strategy 2: Click main popup X — top-right of the overlay
        # Observed position: ~86% from left, ~9% from top
        try:
            x = int(width * 0.86)
            y = int(height * 0.09)
            await page.mouse.click(x, y)
            logger.info(f"Clicked main popup X at ({x}, {y})")
            await asyncio.sleep(0.8)
        except Exception:
            pass

        # Strategy 3: Click bottom-left toast popup X
        # Observed position: ~37% from left, ~80% from top
        try:
            x2 = int(width * 0.37)
            y2 = int(height * 0.80)
            await page.mouse.click(x2, y2)
            logger.info(f"Clicked toast popup X at ({x2}, {y2})")
            await asyncio.sleep(0.8)
        except Exception:
            pass

        # Strategy 4: JS — click ALL visible close/dismiss buttons across the entire page
        try:
            await page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                    for (const btn of btns) {
                        const r = btn.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        const dataName = (btn.getAttribute('data-name') || '').toLowerCase();
                        const text = (btn.textContent || '').trim().toLowerCase();
                        if (label === 'close' || dataName === 'close-button' || text === '×' || text === 'x') {
                            btn.click();
                        }
                    }
                }
            """)
            logger.info("Ran JS sweep for all close buttons")
            await asyncio.sleep(0.8)
        except Exception:
            pass

        logger.info("Popup dismiss complete")

    async def get_chart_snapshot(
        self,
        symbol: str,
        interval: str = "D",
        width: int = 1200,
        height: int = 600,
        theme: str = "dark",
    ) -> Optional[bytes]:
        """Fetch a chart snapshot from TradingView."""
        try:
            context = await self._get_context()
            page = await context.new_page()
            await page.set_viewport_size({"width": width, "height": height})

            chart_url = (
                f"https://www.tradingview.com/chart/?symbol={symbol}"
                f"&interval={interval}&theme={theme}"
            )

            logger.info(f"Loading chart: {symbol} ({interval})")

            try:
                await page.goto(chart_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                await page.goto(chart_url, timeout=45000)

            try:
                await page.wait_for_selector('div[data-name="legend-source-item"]', timeout=20000)
            except Exception:
                try:
                    await page.wait_for_selector(".chart-container", timeout=10000)
                except Exception:
                    pass

            await asyncio.sleep(3)

            # Dismiss any popup before screenshotting
            await self._dismiss_popup(page, width, height)
            await asyncio.sleep(1)

            content = await page.content()
            invalid_indicators = [
                "Invalid symbol",
                "Symbol not found",
                "This symbol is not available",
            ]
            if any(ind in content for ind in invalid_indicators):
                logger.warning(f"Symbol {symbol} invalid on TradingView")
                await page.close()
                return None

            screenshot = await page.screenshot(type="png", full_page=False)
            await page.close()
            if not isinstance(screenshot, bytes):
                logger.error("Screenshot failed to return bytes")
                return None

            logger.info(f"Screenshot captured: {len(screenshot)} bytes")
            return screenshot
        except Exception as e:
            logger.error(f"TradingView capture failed: {e}")
            return None

    async def get_mt5_ohlc(
        self, symbol: str, timeframe_str: str, count: int = 150
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch OHLC data from MT5."""
        tf_map = {
            "1": mt5.TIMEFRAME_M1,
            "5": mt5.TIMEFRAME_M5,
            "15": mt5.TIMEFRAME_M15,
            "30": mt5.TIMEFRAME_M30,
            "60": mt5.TIMEFRAME_H1,
            "240": mt5.TIMEFRAME_H4,
            "D": mt5.TIMEFRAME_D1,
            "W": mt5.TIMEFRAME_W1,
            "M": mt5.TIMEFRAME_MN1,
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_D1)

        login = self.mt5_config.get("login")
        init_params = {
            "login": int(login) if login else 0,
            "password": self.mt5_config.get("password"),
            "server": self.mt5_config.get("server"),
            "path": self.mt5_config.get("path"),
        }
        init_params = {k: v for k, v in init_params.items() if v is not None}

        if not mt5.initialize(**init_params):
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return None

        try:
            mt5_symbol = symbol.split(":")[-1]
            rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                logger.error(f"No MT5 data for {mt5_symbol}")
                return None

            return [
                {
                    "time": int(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                }
                for r in rates
            ]
        except Exception as e:
            logger.error(f"MT5 data error: {e}")
            return None
        finally:
            pass

    async def render_ohlc_chart(
        self,
        data: List[Dict],
        symbol: str,
        theme: str = "dark",
        width: int = 1200,
        height: int = 600,
    ) -> Optional[bytes]:
        """Render OHLC data using internal template."""
        tmp_path = None
        try:
            template_path = Path(__file__).parent / "chart_template.html"
            if not template_path.exists():
                logger.error(f"Template not found at {template_path}")
                return None

            context = await self._get_context()
            page = await context.new_page()
            await page.set_viewport_size({"width": width, "height": height})

            with open(template_path, "r") as f:
                html = f.read()

            safe_symbol = symbol.replace('"', '\\"').replace("'", "\\'")
            data_injection = f"""
            <script>
                window.chartData = {{
                    data: {json.dumps(data)},
                    symbol: "{safe_symbol}",
                    theme: "{theme}"
                }};
                (function waitAndInit() {{
                    if (typeof LightweightCharts !== 'undefined') {{
                        initChart(window.chartData.data, window.chartData.symbol, window.chartData.theme);
                    }} else {{ setTimeout(waitAndInit, 50); }}
                }})();
            </script>
            """
            html = html.replace("</body>", f"{data_injection}</body>")

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", delete=False, encoding="utf-8"
            ) as f:
                f.write(html)
                tmp_path = f.name

            file_url = Path(tmp_path).as_uri()
            await page.goto(file_url, wait_until="networkidle", timeout=30000)
            try:
                await page.wait_for_function("window.chartReady === true", timeout=10000)
            except Exception:
                logger.warning("window.chartReady not set; chart may be empty or errored")
                pass

            await asyncio.sleep(0.5)
            screenshot = await page.screenshot(type="png")
            await page.close()
            return screenshot
        except Exception as e:
            logger.error(f"Render failed: {e}")
            try:
                await page.close()
            except Exception:
                pass
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    async def validate_session(self) -> bool:
        """Verify TradingView authentication."""
        try:
            context = await self._get_context()
            page = await context.new_page()
            await page.goto("https://www.tradingview.com/", timeout=15000)
            await asyncio.sleep(1)
            content = await page.content()
            await page.close()
            is_authenticated = "sign in" not in content.lower() or "user-menu" in content.lower()
            return is_authenticated
        except Exception as e:
            logger.error(f"Session validation failed: {e}")
            return False

    async def close(self):
        """Clean up resources."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


# MCP server initialization
app = Server("tradingview-mcp")


async def get_client() -> TradingViewClient:
    """Get the global client instance."""
    global _client
    if _client is None:
        _client = TradingViewClient()
    return _client


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="get_chart_snapshot",
            description="Fetch a chart snapshot for a given symbol and timeframe. "
            "Automatically falls back to MetaTrader 5 rendering if the symbol is not found on TradingView. "
            "Returns the chart as a base64-encoded PNG image. "
            "Symbol format: 'EXCHANGE:SYMBOL' or just 'SYMBOL' (e.g., 'BINANCE:BTCUSDT', 'Volatility 50 Index').",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol (e.g., 'BINANCE:BTCUSDT' or 'EURUSD')",
                    },
                    "interval": {
                        "type": "string",
                        "description": "Chart interval: 1, 5, 15, 30, 60, 240 (minutes) or D, W, M",
                        "default": "D",
                    },
                    "width": {
                        "type": "number",
                        "description": "Image width in pixels (default: 1200)",
                        "default": 1200,
                    },
                    "height": {
                        "type": "number",
                        "description": "Image height in pixels (default: 600)",
                        "default": 600,
                    },
                    "theme": {
                        "type": "string",
                        "description": "Chart theme: 'dark' or 'light' (default: dark)",
                        "default": "dark",
                        "enum": ["dark", "light"],
                    },
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="render_ohlc_chart",
            description="Render a chart from a provided OHLC data array using the Lightweight Charts library. "
            "Expected data format: list of objects with {time, open, high, low, close}. "
            "Time: Unix timestamp (seconds) or 'YYYY-MM-DD'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ohlc_data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of OHLC data points (from MetaTrader or other sources)",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name to display (e.g., 'EURUSD')",
                    },
                    "width": {
                        "type": "number",
                        "description": "Image width (default: 1200)",
                        "default": 1200,
                    },
                    "height": {
                        "type": "number",
                        "description": "Image height (default: 600)",
                        "default": 600,
                    },
                    "theme": {
                        "type": "string",
                        "description": "Chart theme: 'dark' or 'light'",
                        "default": "dark",
                        "enum": ["dark", "light"],
                    },
                },
                "required": ["ohlc_data", "symbol"],
            },
        ),
        Tool(
            name="validate_session",
            description="Validate if the TradingView session credentials are working correctly.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_timeframes",
            description="List all available timeframes/intervals for TradingView charts.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Handle tool calls."""
    client = await get_client()

    if name == "get_chart_snapshot":
        symbol = arguments.get("symbol")
        interval = str(arguments.get("interval", "D"))
        width = int(arguments.get("width", 1200))
        height = int(arguments.get("height", 600))
        theme = str(arguments.get("theme", "dark"))

        if not symbol:
            return [TextContent(type="text", text="Error: 'symbol' parameter is required.")]

        logger.info(f"Fetching chart for {symbol} ({interval}) [With Auto-Fallback]")

        # Attempt 1: TradingView
        image_data = await client.get_chart_snapshot(str(symbol), interval, width, height, theme)
        source = "TradingView (Snapshot)"

        # Attempt 2: Retry
        if not image_data:
            logger.info("TradingView attempt 1 failed, retrying...")
            image_data = await client.get_chart_snapshot(
                str(symbol), interval, width, height, theme
            )

        if not image_data:
            logger.info("TradingView failed. Falling back to MetaTrader 5...")
            ohlc_data = await client.get_mt5_ohlc(str(symbol), interval)
            if ohlc_data:
                image_data = await client.render_ohlc_chart(
                    ohlc_data, str(symbol), theme, width, height
                )
                source = "MetaTrader 5 (Lightweight Charts fallback)"
            else:
                return [
                    TextContent(
                        type="text",
                        text=f"Failed to fetch data from both TradingView and MetaTrader 5 for {symbol}.",
                    )
                ]

        if image_data:
            image_base64 = base64.b64encode(image_data).decode("utf-8")
            return [
                TextContent(
                    type="text",
                    text=f"Chart for {symbol} ({interval})\nSource: {source}\nSize: {width}x{height}",
                ),
                ImageContent(type="image", data=image_base64, mimeType="image/png"),
            ]
        return [TextContent(type="text", text="Failed to generate chart image.")]

    elif name == "validate_session":
        is_valid = await client.validate_session()
        status = "✓ Valid" if is_valid else "✗ Invalid"
        return [
            TextContent(
                type="text",
                text=f"Session Status: {status}\n\n"
                f"The TradingView session credentials are {'working correctly' if is_valid else 'not working'}.",
            )
        ]

    elif name == "list_timeframes":
        timeframes = {
            "Minutes": ["1", "5", "15", "30", "60", "240"],
            "Days/Weeks/Months": ["D", "W", "M"],
        }

        result = "Available TradingView Timeframes:\n\n"
        for category, intervals in timeframes.items():
            result += f"{category}:\n"
            for interval in intervals:
                result += f"  - {interval}\n"

        result += "\nExamples:\n"
        result += "  - '5' = 5-minute chart\n"
        result += "  - '60' = 1-hour chart\n"
        result += "  - 'D' = Daily chart\n"

        return [TextContent(type="text", text=result)]

    elif name == "render_ohlc_chart":
        ohlc_data = arguments.get("ohlc_data")
        symbol = arguments.get("symbol")
        width = int(arguments.get("width", 1200))
        height = int(arguments.get("height", 600))
        theme = arguments.get("theme", "dark")

        if not ohlc_data or not symbol:
            return [TextContent(type="text", text="Error: 'ohlc_data' and 'symbol' are required.")]

        if not isinstance(ohlc_data, list):
            return [TextContent(type="text", text="Error: 'ohlc_data' must be an array.")]

        logger.info(f"Rendering OHLC chart for {symbol}")
        image_data = await client.render_ohlc_chart(ohlc_data, str(symbol), theme, width, height)

        if image_data:
            image_base64 = base64.b64encode(image_data).decode("utf-8")
            return [
                TextContent(
                    type="text",
                    text=f"Rendered chart for {symbol}\nSource: External Data (Lightweight Charts)\nSize: {width}x{height} | Theme: {theme}",
                ),
                ImageContent(type="image", data=image_base64, mimeType="image/png"),
            ]
        else:
            return [TextContent(type="text", text="Failed to render chart image.")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    logger.info("Starting TradingView MCP Server with Playwright...")

    if not os.getenv("TRADINGVIEW_SESSION_ID") or not os.getenv("TRADINGVIEW_SESSION_ID_SIGN"):
        logger.warning(
            "Warning: TradingView credentials not found in environment. "
            "Please set TRADINGVIEW_SESSION_ID and TRADINGVIEW_SESSION_ID_SIGN in .env file."
        )

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        if _client:
            await _client.close()


if __name__ == "__main__":
    asyncio.run(main())