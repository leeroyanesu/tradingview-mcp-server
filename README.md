# TradingView MCP Server 📈

A **lightweight** Model Context Protocol (MCP) server for fetching TradingView chart snapshots with **automatic MetaTrader 5 fallback**. Uses Playwright for efficient browser automation with persistent sessions and cookie-based authentication. When TradingView fails or a symbol isn't available, it seamlessly renders charts from live MT5 OHLC data using [Lightweight Charts](https://tradingview.github.io/lightweight-charts/).

> **Note**: This uses Playwright because TradingView renders charts client-side with JavaScript/Canvas. There is no pure HTTP API for chart images. This is the lightest possible working solution (~150MB RAM vs ~500MB with Selenium).

## ✨ Features

- 🪶 **Lightweight**: Playwright in headless mode (~150MB vs ~500MB with Selenium)
- 🚀 **Fast**: Persistent browser reuse, 3-5 seconds per chart
- 🔐 **Secure**: Session-based authentication via cookies
- 🎨 **Customizable**: Configure chart dimensions, intervals, and themes
- 🔧 **MCP Compatible**: Works with any MCP-enabled client (Claude Desktop, etc.)
- ♻️ **Efficient**: Reuses browser instances across multiple requests
- 📉 **MT5 Fallback**: Automatically fetches OHLC data from MetaTrader 5 and renders via Lightweight Charts when TradingView fails
- 🕯️ **Offline Chart Rendering**: Render any OHLC dataset as a candlestick chart via the `render_ohlc_chart` tool — no TradingView session required
- 📊 **150 candles by default**: MT5 fallback fetches 150 candles for richer historical context

## 📋 Prerequisites

- Python 3.10 or higher
- A TradingView account (free or paid)
- TradingView session cookies
- *(Optional, for fallback)* MetaTrader 5 terminal installed and running with a valid account

## Table of Contents

- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Usage](#-usage)
- [Configuration](#-configuration)
- [Available Tools](#️-available-tools)
- [Symbol Format](#-symbol-format)
- [MT5 Fallback](#-mt5-fallback)
- [Troubleshooting](#-troubleshooting)
- [Performance](#-performance)
- [Contributing](#-contributing)

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/leeroyanesu/tradingview-mcp.git
cd tradingview-mcp
```

### 2. Create a virtual environment

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt

# Install Playwright browsers (one-time setup, ~150MB download)
python -m playwright install chromium
```

### 4. Configure credentials

1. Copy `.env.example` to `.env`:
   ```bash
   # Windows
   copy .env.example .env

   # Linux/Mac
   cp .env.example .env
   ```

2. **Get your TradingView session cookies:**
   - Log into [TradingView](https://www.tradingview.com) in your browser
   - Press **F12** to open Developer Tools
   - Go to **Application** tab (Chrome) or **Storage** tab (Firefox)
   - Navigate to **Cookies** > `https://www.tradingview.com`
   - Find and copy these two cookie values:
     - `sessionid` → Copy the entire value
     - `sessionid_sign` → Copy the entire value

   > ⚠️ **Important**: Copy the full values including any special characters (slashes, equals signs, etc.). Don't add quotes or extra spaces.

3. Edit `.env` and paste your values:
   ```env
   TRADINGVIEW_SESSION_ID=your_actual_session_id_here
   TRADINGVIEW_SESSION_ID_SIGN=your_actual_session_id_sign_here

   # Optional: MetaTrader 5 credentials for automatic fallback
   MT5_LOGIN=40931844
   MT5_PASSWORD=your_mt5_password
   MT5_SERVER=Deriv-Demo
   MT5_PATH=C:\Program Files\MetaTrader 5 Terminal\terminal64.exe
   ```

### 5. Test your setup (Optional but recommended)

You can test the library import and basic functionality:

```bash
# Windows
set PYTHONPATH=src
python -c "from tradingview_mcp.server import TradingViewClient; print('Import successful')"

# Linux/Mac
export PYTHONPATH=src
python3 -c "from tradingview_mcp.server import TradingViewClient; print('Import successful')"
```

Or run the bundled test script:

```bash
python test_tradingview_direct.py
```

## 🎯 Usage

### Quick Start - Testing Locally

```bash
python src/tradingview_mcp/server.py
```

The server will start and wait for MCP client connections via stdio.

### Configuration for Claude Desktop

Add this to your Claude Desktop config file:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Linux**: `~/.config/Claude/claude_desktop_config.json`

**Option 1: Using .env file (Recommended)**
```json
{
  "mcpServers": {
    "tradingview": {
      "command": "python",
      "args": ["C:\\path\\to\\tradingview-mcp\\src\\tradingview_mcp\\server.py"],
      "cwd": "C:\\path\\to\\tradingview-mcp"
    }
  }
}
```

**Option 2: Inline credentials**
```json
{
  "mcpServers": {
    "tradingview": {
      "command": "python",
      "args": ["C:\\path\\to\\tradingview-mcp\\src\\tradingview_mcp\\server.py"],
      "env": {
        "TRADINGVIEW_SESSION_ID": "your_session_id",
        "TRADINGVIEW_SESSION_ID_SIGN": "your_session_id_sign",
        "MT5_LOGIN": "40931844",
        "MT5_PASSWORD": "your_mt5_password",
        "MT5_SERVER": "Deriv-Demo",
        "MT5_PATH": "C:\\Program Files\\MetaTrader 5 Terminal\\terminal64.exe"
      }
    }
  }
}
```

> 💡 **Tip**: Use absolute paths. On Windows, use double backslashes `\\` or forward slashes `/`.

**Restart Claude Desktop** and you'll see the TradingView tools available!

### Python Library Usage

You can use the `TradingViewClient` directly in your Python projects:

```python
import asyncio
from tradingview_mcp.server import TradingViewClient
from dotenv import load_dotenv

async def main():
    load_dotenv()
    
    # Initialize client (uses .env by default)
    client = TradingViewClient()
    
    # Fetch a chart snapshot
    # Automatically falls back to MT5 if TradingView fails
    image = await client.get_chart_snapshot("BINANCE:BTCUSDT", interval="D")
    
    if image:
        with open("btc_chart.png", "wb") as f:
            f.write(image)
        print("Chart saved!")
    
    # Don't forget to close!
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

> **Note**: When running as a library from the source, ensure `src` is in your `PYTHONPATH` or install in editable mode: `pip install -e .`

## 🛠️ Available Tools

### 1. `get_chart_snapshot`

Fetch a TradingView chart snapshot with **automatic MT5 fallback**. If the symbol isn't found on TradingView (or the session fails), it transparently fetches live OHLC data from MetaTrader 5 and renders a candlestick chart using Lightweight Charts.

**Parameters:**
- `symbol` (required): Trading pair in TradingView format or MT5 name
  - Examples: `"BINANCE:BTCUSDT"`, `"NASDAQ:AAPL"`, `"Volatility 50 Index"`
- `interval` (optional): Chart timeframe (default: `"D"`)
  - Minutes: `"1"`, `"5"`, `"15"`, `"30"`, `"60"`, `"240"`
  - Days/Weeks/Months: `"D"`, `"W"`, `"M"`
- `width` (optional): Image width in pixels (default: `1200`)
- `height` (optional): Image height in pixels (default: `600`)
- `theme` (optional): `"dark"` or `"light"` (default: `"dark"`)

**Fallback behavior:**
1. Attempts TradingView snapshot (up to 2 tries)
2. If both fail → fetches 150 candles from MetaTrader 5
3. Renders an interactive candlestick chart via Lightweight Charts v4
4. Returns the chart image with source attribution

**Example:**
```
Get me a daily chart of Volatility 50 Index
```

---

### 2. `render_ohlc_chart`

Render a candlestick chart directly from a provided OHLC data array — no TradingView session needed. Accepts data from any source (MT5, CSV, API, etc.).

**Parameters:**
- `ohlc_data` (required): Array of OHLC objects
  ```json
  [
    {"time": 1701820800, "open": 136.78, "high": 142.84, "low": 135.44, "close": 141.94},
    ...
  ]
  ```
  Time can be a Unix timestamp (seconds) **or** a date string `"YYYY-MM-DD"`.
- `symbol` (required): Symbol name to display on the chart (e.g., `"Volatility 50 Index"`)
- `width` (optional): Image width (default: `1200`)
- `height` (optional): Image height (default: `600`)
- `theme` (optional): `"dark"` or `"light"` (default: `"dark"`)

**Example:**
```
Render a chart from this OHLC data for Volatility 50 Index
```

---

### 3. `validate_session`

Check if your TradingView session credentials are valid.

**Example:**
```
Validate my TradingView session
```

---

### 4. `list_timeframes`

List all available chart timeframes/intervals.

**Example:**
```
What timeframes are available?
```

## 📉 MT5 Fallback

When TradingView cannot render a chart (invalid session, symbol not on TradingView, network issues), the server automatically:

1. Initializes MetaTrader 5 using credentials from `.env`
2. Fetches **150 OHLC candles** for the requested symbol and timeframe
3. Renders a full-screen candlestick chart using **Lightweight Charts v4.2.0** (pinned to avoid v5 breaking changes)
4. Returns the PNG screenshot with source: `MetaTrader 5 (Lightweight Charts fallback)`

> **Tip**: Symbols like `"Volatility 50 Index"` exist only on Deriv/MT5 brokers — the fallback is designed exactly for these cases.

### MT5 `.env` Variables

```env
MT5_LOGIN=40931844          # Your MT5 account number (integer)
MT5_PASSWORD=your_password
MT5_SERVER=Deriv-Demo       # Your broker's server name
MT5_PATH=C:\Program Files\MetaTrader 5 Terminal\terminal64.exe
```

## 📊 Symbol Format

TradingView uses the format: `EXCHANGE:SYMBOL`

### Common Examples

| Symbol | Description |
|--------|-------------|
| `BINANCE:BTCUSDT` | Bitcoin/USDT on Binance |
| `NASDAQ:AAPL` | Apple Inc. on NASDAQ |
| `NYSE:TSLA` | Tesla Inc. on NYSE |
| `BITSTAMP:BTCUSD` | Bitcoin/USD on Bitstamp |
| `FX:EURUSD` | EUR/USD forex pair |
| `COINBASE:ETHUSD` | Ethereum/USD on Coinbase |
| `Volatility 50 Index` | Deriv synthetic (MT5 fallback) |

## 🔧 Architecture

```
get_chart_snapshot call
│
├── Attempt 1: TradingView Playwright snapshot
├── Attempt 2: TradingView retry
│
└── Fallback: MetaTrader 5
    ├── mt5.initialize() with .env credentials
    ├── copy_rates_from_pos() → 150 OHLC candles
    └── render_lightweight_chart()
        ├── Injects data into chart_template.html
        ├── Loads via file:// URL (ensures CDN scripts load)
        ├── Waits for networkidle + window.chartReady
        └── Screenshots PNG
```

**Chart Renderer Details:**
- Uses **Lightweight Charts v4.2.0** (pinned — v5 removed `addCandlestickSeries`)
- Loaded via `file://` URL in Playwright so the CDN script is guaranteed to execute
- `window.chartReady` flag used for reliable render detection instead of blind `sleep()`

## 🐛 Troubleshooting

### MT5 Login Fails

**Problem**: `(-2, 'Invalid "login" argument')`

**Solution**: Ensure `MT5_LOGIN` is stored as a plain integer in `.env` — no quotes. The server casts it with `int()` automatically.

### Cookie/Authentication Issues

**Problem**: "Session credentials are not working" or "Invalid session"

**Solutions**:
1. No trailing commas/spaces in `.env`
2. Refresh cookies — they expire after ~30 days
3. Make sure you were logged in when copying cookies

### Symbol Not Found

**Problem**: "Failed to fetch chart snapshot" and MT5 fallback returns no data

**Solutions**:
1. Use correct TradingView format: `EXCHANGE:SYMBOL` (uppercase)
2. For synthetic/broker-specific symbols, use the **exact MT5 symbol name** (e.g., `Volatility 50 Index`)
3. Verify the symbol is available in your MT5 Market Watch

### Timeout Errors

**Problem**: "Timeout exceeded" during chart rendering

**Solutions**:
1. Check internet connection (CDN script loads from unpkg.com)
2. First request takes longer (5-8s) due to browser startup
3. Subsequent requests are faster (3-5s) due to browser reuse

### Playwright Installation Issues

```bash
pip uninstall playwright
pip install playwright
python -m playwright install chromium
```

### Claude Desktop Not Recognizing Server

1. Check JSON syntax in config (use a JSON validator)
2. Use **absolute paths** only
3. Verify Python path: `where python` (Windows)
4. **Completely restart** Claude Desktop
5. Check logs: `%APPDATA%\Claude\logs\`

## ⚡ Performance

| Metric | Value | Notes |
|--------|-------|-------|
| **Memory Usage** | ~150MB | Playwright headless browser |
| **First Request** | 5-8 seconds | Browser startup + chart load |
| **Subsequent Requests** | 3-5 seconds | Browser reuse (persistent) |
| **MT5 Fallback** | 3-6 seconds | MT5 init + render |
| **Image Size** | 50-200KB | PNG format |

## 🔒 Security Notes

- ⚠️ **Never commit your `.env` file** — it contains session credentials and MT5 credentials
- ⚠️ **Don't share your session cookies** — they provide full TradingView account access
- ⚠️ **Rotate cookies regularly** — they expire after ~30 days
- ✅ `.env` is in `.gitignore` by default

## 📝 Project Structure

```
tradingview-mcp/
├── src/
│   └── tradingview_mcp/
│       ├── __init__.py
│       ├── server.py              # Main MCP server + MT5 fallback
│       └── chart_template.html   # Lightweight Charts v4 template
├── test_mt5_direct.py             # MT5 connection test script
├── .env.example                   # Template for credentials
├── .gitignore
├── README.md
├── requirements.txt
└── pyproject.toml
```

## 🤝 Contributing

1. Fork the repository on [GitHub](https://github.com/leeroyanesu/tradingview-mcp)
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes and test thoroughly
4. Commit (`git commit -m 'Add amazing feature'`)
5. Push (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [TradingView](https://www.tradingview.com) - Excellent charting platform
- [Anthropic](https://www.anthropic.com) - Model Context Protocol
- [Playwright](https://playwright.dev) - Browser automation
- [Lightweight Charts](https://tradingview.github.io/lightweight-charts/) - Chart rendering for MT5 fallback
- [MetaTrader5](https://pypi.org/project/MetaTrader5/) - Python MT5 integration

## ⚠️ Disclaimer

This is an **unofficial** tool and is not affiliated with, endorsed by, or connected to TradingView. Use at your own risk and in accordance with TradingView's Terms of Service.

## 🆘 Support & Issues

- 🐛 **Bug Reports**: [GitHub Issues](https://github.com/leeroyanesu/tradingview-mcp/issues)
- 💬 **Questions**: [GitHub Discussions](https://github.com/leeroyanesu/tradingview-mcp/discussions)

## 📊 Project Stats

- **Version**: 0.3.0
- **Python**: 3.10+
- **Memory**: ~150MB
- **Response Time**: 3-8 seconds
- **License**: MIT

---

**Made with ❤️ by [leeroyanesu](https://github.com/leeroyanesu)**

*Star ⭐ this repo if you find it useful!*
