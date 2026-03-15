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
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext
import MetaTrader5 as mt5
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent
from mcp.server.stdio import stdio_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Global browser instance (reused for efficiency)
_playwright = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None


async def get_browser_context() -> BrowserContext:
    """Get or create a persistent browser context with TradingView authentication."""
    global _playwright, _browser, _context
    
    if _context is not None:
        return _context
    
    session_id = os.getenv("TRADINGVIEW_SESSION_ID")
    session_id_sign = os.getenv("TRADINGVIEW_SESSION_ID_SIGN")
    
    if not session_id or not session_id_sign:
        raise ValueError("TradingView credentials not found in environment")
    
    # Start Playwright
    if _playwright is None:
        _playwright = await async_playwright().start()
    
    # Launch browser in headless mode (lightweight)
    if _browser is None:
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu'
            ]
        )
        logger.info("Browser launched successfully")
    
    # Create context with cookies
    if _browser is None:
        raise RuntimeError("Browser failed to launch")
        
    _context = await _browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    if _context is None:
        raise RuntimeError("Failed to create browser context")
        
    # Add TradingView cookies
    await _context.add_cookies([
        {
            'name': 'sessionid',
            'value': session_id,
            'domain': '.tradingview.com',
            'path': '/',
            'httpOnly': True,
            'secure': True,
            'sameSite': 'Lax'
        },
        {
            'name': 'sessionid_sign',
            'value': session_id_sign,
            'domain': '.tradingview.com',
            'path': '/',
            'httpOnly': True,
            'secure': True,
            'sameSite': 'Lax'
        }
    ])
    
    logger.info("Browser context created with authentication")
    return _context


async def get_chart_snapshot(
    symbol: str,
    interval: str = "D",
    width: int = 1200,
    height: int = 600,
    theme: str = "dark"
) -> Optional[bytes]:
    """
    Fetch a TradingView chart snapshot.
    
    Args:
        symbol: Trading symbol (e.g., "BINANCE:BTCUSDT")
        interval: Chart interval (1, 5, 15, 30, 60, 240, D, W, M)
        width: Image width
        height: Image height
        theme: Chart theme (dark or light)
    
    Returns:
        PNG image bytes or None if failed
    """
    try:
        context = await get_browser_context()
        page = await context.new_page()
        
        # Set viewport
        await page.set_viewport_size({"width": width, "height": height})
        
        # Build TradingView chart URL
        chart_url = (
            f"https://www.tradingview.com/chart/?symbol={symbol}"
            f"&interval={interval}"
            f"&theme={theme}"
        )
        
        logger.info(f"Loading chart: {symbol} ({interval})")
        
        # Navigate to chart with longer timeout
        try:
            await page.goto(chart_url, wait_until="domcontentloaded", timeout=45000)
        except:
            # Fallback: try without waiting for full network idle
            await page.goto(chart_url, timeout=45000)
        
        # Wait for chart to load (with fallback)
        try:
            await page.wait_for_selector('div[data-name="legend-source-item"]', timeout=20000)
        except:
            # Alternative selector if the first one doesn't work
            try:
                await page.wait_for_selector('.chart-container', timeout=10000)
            except:
                pass  # Continue anyway
        
        # Additional wait for chart rendering
        await asyncio.sleep(3)
        
        # Check if the symbol is valid by looking for common error indicators
        content = await page.content()
        invalid_indicators = [
            "Invalid symbol",
            "Symbol not found",
            "This symbol is not available"
        ]
        
        is_invalid = any(indicator in content for indicator in invalid_indicators)
        if is_invalid:
            logger.warning(f"Symbol {symbol} appears to be invalid on TradingView.")
            await page.close()
            return None
            
        # Take screenshot
        screenshot = await page.screenshot(type='png', full_page=False)
        
        await page.close()
        if not isinstance(screenshot, bytes):
            logger.error("Screenshot failed to return bytes")
            return None
            
        logger.info(f"Screenshot captured: {len(screenshot)} bytes")
        return screenshot
        
    except Exception as e:
        logger.error(f"Failed to capture chart: {e}")
        return None



async def get_mt5_ohlc(symbol: str, timeframe_str: str, count: int = 150) -> Optional[List[Dict[str, Any]]]:
    """Fetch OHLC data from MetaTrader 5."""
    # Map TradingView intervals to MT5 timeframes
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
    
    # Initialize MT5 with credentials from .env
    mt5_login = os.getenv("MT5_LOGIN")
    init_params = {
        "login": int(mt5_login) if mt5_login else 0,
        "password": os.getenv("MT5_PASSWORD"),
        "server": os.getenv("MT5_SERVER"),
        "path": os.getenv("MT5_PATH")
    }
    init_params = {k: v for k, v in init_params.items() if v is not None}
    
    if not mt5.initialize(**init_params):
        logger.error(f"MT5 initialization failed: {mt5.last_error()}")
        return None
        
    try:
        # Strip exchange prefix for MT5
        mt5_symbol = symbol.split(':')[-1]
        
        rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.error(f"No MT5 data for {mt5_symbol}")
            return None
            
        data = []
        for rate in rates:
            data.append({
                "time": int(rate['time']),
                "open": float(rate['open']),
                "high": float(rate['high']),
                "low": float(rate['low']),
                "close": float(rate['close'])
            })
        return data
    except Exception as e:
        logger.error(f"Error fetching MT5 data: {e}")
        return None
    finally:
        pass


async def render_lightweight_chart(
    data: List[Dict], 
    symbol: str, 
    theme: str = "dark",
    width: int = 1200,
    height: int = 600
) -> Optional[bytes]:
    """Render data using Lightweight Charts via Playwright and take a screenshot."""
    tmp_path = None
    try:
        template_path = Path(__file__).parent / "chart_template.html"
        if not template_path.exists():
            logger.error(f"Template not found at {template_path}")
            return None
            
        context = await get_browser_context()
        page = await context.new_page()
        await page.set_viewport_size({"width": width, "height": height})
        
        # Build the HTML content with data injected
        with open(template_path, 'r') as f:
            html = f.read()
        
        # Escape symbol for JS string safety
        safe_symbol = symbol.replace('"', '\\"').replace("'", "\\'")
            
        # Inject data before the closing body tag
        data_injection = f"""
        <script>
            window.chartData = {{
                data: {json.dumps(data)},
                symbol: "{safe_symbol}",
                theme: "{theme}"
            }};
            // Defer until library is confirmed loaded
            (function waitAndInit() {{
                if (typeof LightweightCharts !== 'undefined') {{
                    initChart(window.chartData.data, window.chartData.symbol, window.chartData.theme);
                }} else {{
                    setTimeout(waitAndInit, 50);
                }}
            }})();
        </script>
        """
        html = html.replace('</body>', f'{data_injection}</body>')
        
        # Write to a temp file and load via file:// so external CDN scripts load properly.
        # page.set_content() can block external script loading in some Playwright versions.
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(html)
            tmp_path = f.name
        
        file_url = Path(tmp_path).as_uri()
        # networkidle ensures the CDN script has finished loading
        await page.goto(file_url, wait_until="networkidle", timeout=30000)
        
        # Wait for initChart to complete and set window.chartReady = true
        try:
            await page.wait_for_function("window.chartReady === true", timeout=10000)
        except Exception:
            # If chartReady never became true, take a screenshot anyway for diagnostics
            logger.warning("window.chartReady not set; chart may be empty or errored")
        
        # Small buffer for final paint
        await asyncio.sleep(0.5)
        
        # Take screenshot
        screenshot = await page.screenshot(type='png')
        await page.close()
        
        return screenshot
    except Exception as e:
        logger.error(f"Failed to render lightweight chart: {e}")
        try:
            await page.close()
        except Exception:
            pass
        return None
    finally:
        if tmp_path:
            try:
                import os as _os
                _os.unlink(tmp_path)
            except Exception:
                pass


async def validate_session() -> bool:
    """Validate if TradingView session is working."""
    try:
        context = await get_browser_context()
        page = await context.new_page()
        
        await page.goto("https://www.tradingview.com/", timeout=15000)
        
        # Check if we're logged in (look for user menu or profile indicators)
        await asyncio.sleep(1)
        content = await page.content()
        
        await page.close()
        
        # If we see login/signin, we're NOT authenticated
        is_authenticated = 'sign in' not in content.lower() or 'user-menu' in content.lower()
        
        return is_authenticated
        
    except Exception as e:
        logger.error(f"Session validation failed: {e}")
        return False


async def cleanup():
    """Cleanup browser resources."""
    global _browser, _context, _playwright
    
    if _context:
        await _context.close()
        _context = None
    
    if _browser:
        await _browser.close()
        _browser = None
    
    if _playwright:
        await _playwright.stop()
        _playwright = None


# Initialize MCP server
app = Server("tradingview-mcp")


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
                        "description": "Trading symbol (e.g., 'BINANCE:BTCUSDT' or 'EURUSD')"
                    },
                    "interval": {
                        "type": "string",
                        "description": "Chart interval: 1, 5, 15, 30, 60, 240 (minutes) or D, W, M",
                        "default": "D"
                    },
                    "width": {
                        "type": "number",
                        "description": "Image width in pixels (default: 1200)",
                        "default": 1200
                    },
                    "height": {
                        "type": "number",
                        "description": "Image height in pixels (default: 600)",
                        "default": 600
                    },
                    "theme": {
                        "type": "string",
                        "description": "Chart theme: 'dark' or 'light' (default: dark)",
                        "default": "dark",
                        "enum": ["dark", "light"]
                    }
                },
                "required": ["symbol"]
            }
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
                        "items": { "type": "object" },
                        "description": "Array of OHLC data points (from MetaTrader or other sources)"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name to display (e.g., 'EURUSD')"
                    },
                    "width": {
                        "type": "number",
                        "description": "Image width (default: 1200)",
                        "default": 1200
                    },
                    "height": {
                        "type": "number",
                        "description": "Image height (default: 600)",
                        "default": 600
                    },
                    "theme": {
                        "type": "string",
                        "description": "Chart theme: 'dark' or 'light'",
                        "default": "dark",
                        "enum": ["dark", "light"]
                    }
                },
                "required": ["ohlc_data", "symbol"]
            }
        ),
        Tool(
            name="validate_session",
            description="Validate if the TradingView session credentials are working correctly.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="list_timeframes",
            description="List all available timeframes/intervals for TradingView charts.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Handle tool calls."""
    
    # Check credentials
    session_id = os.getenv("TRADINGVIEW_SESSION_ID")
    session_id_sign = os.getenv("TRADINGVIEW_SESSION_ID_SIGN")
    
    if not session_id or not session_id_sign:
        return [TextContent(
            type="text",
            text="Error: TradingView credentials not found. Please set TRADINGVIEW_SESSION_ID and "
                 "TRADINGVIEW_SESSION_ID_SIGN in your .env file."
        )]
    
    elif name == "get_chart_snapshot":
        symbol = arguments.get("symbol")
        interval = str(arguments.get("interval", "D"))
        width = int(arguments.get("width", 1200))
        height = int(arguments.get("height", 600))
        theme = str(arguments.get("theme", "dark"))
        
        if not symbol:
            return [TextContent(type="text", text="Error: 'symbol' parameter is required.")]
            
        logger.info(f"Fetching chart for {symbol} ({interval}) [With Auto-Fallback]")
        
        # Attempt 1: TradingView
        image_data = await get_chart_snapshot(str(symbol), interval, width, height, theme)
        
        # Attempt 2: Retry
        if not image_data:
            logger.info("TradingView attempt 1 failed, retrying...")
            image_data = await get_chart_snapshot(str(symbol), interval, width, height, theme)
            
        source = "TradingView (Snapshot)"
        if not image_data:
            logger.info("TradingView failed. Falling back to MetaTrader 5...")
            ohlc_data = await get_mt5_ohlc(str(symbol), interval)
            if ohlc_data:
                image_data = await render_lightweight_chart(ohlc_data, str(symbol), theme, width, height)
                source = "MetaTrader 5 (Lightweight Charts fallback)"
            else:
                return [TextContent(type="text", text=f"Failed to fetch data from both TradingView and MetaTrader 5 for {symbol}.")]
                
        if image_data:
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            return [
                TextContent(
                    type="text",
                    text=f"Chart for {symbol} ({interval})\nSource: {source}\nSize: {width}x{height}"
                ),
                ImageContent(
                    type="image",
                    data=image_base64,
                    mimeType="image/png"
                )
            ]
        return [TextContent(type="text", text="Failed to generate chart image.")]

    elif name == "validate_session":
        is_valid = await validate_session()
        status = "✓ Valid" if is_valid else "✗ Invalid"
        return [TextContent(
            type="text",
            text=f"Session Status: {status}\n\n"
                 f"The TradingView session credentials are {'working correctly' if is_valid else 'not working'}."
        )]
    
    elif name == "list_timeframes":
        timeframes = {
            "Minutes": ["1", "5", "15", "30", "60", "240"],
            "Days/Weeks/Months": ["D", "W", "M"]
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
        image_data = await render_lightweight_chart(ohlc_data, str(symbol), theme, width, height)
        
        if image_data:
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            return [
                TextContent(
                    type="text",
                    text=f"Rendered chart for {symbol}\nSource: External Data (Lightweight Charts)\nSize: {width}x{height} | Theme: {theme}"
                ),
                ImageContent(
                    type="image",
                    data=image_base64,
                    mimeType="image/png"
                )
            ]
        else:
            return [TextContent(type="text", text="Failed to render chart image.")]


    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    logger.info("Starting TradingView MCP Server with Playwright...")
    
    # Validate environment variables
    if not os.getenv("TRADINGVIEW_SESSION_ID") or not os.getenv("TRADINGVIEW_SESSION_ID_SIGN"):
        logger.warning(
            "Warning: TradingView credentials not found in environment. "
            "Please set TRADINGVIEW_SESSION_ID and TRADINGVIEW_SESSION_ID_SIGN in .env file."
        )
    
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
