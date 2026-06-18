"""
Screener.in MCP Server — Indian Stock Research Assistant

Tools exposed to Claude:
  search_company          — find a company by name or symbol
  get_company_overview    — key ratios, about, price data
  get_financials          — P&L / Balance Sheet / Cash Flow / Ratios history
  get_quarterly_results   — last 8 quarters of results
  get_shareholding        — promoter / FII / DII / public holding trend
  get_peers               — peer comparison table
  compare_companies       — side-by-side comparison of 2-5 companies
  screen_stocks           — custom Screener.in query
  screen_by_theme         — pre-built thematic screens
  list_themes             — list available theme screens
  get_full_analysis       — ALL data for deep-dive reasoning
  get_red_flags           — structured red flag checklist data
  beginner_explainer      — data + prompt for beginner-friendly explanation

Resources:
  screener://analyst-guide    — how to use this assistant
  screener://query-syntax     — Screener query language reference

Credentials:
  * stdio transport — set environment variables:
        SCREENER_USERNAME=your@email.com
        SCREENER_PASSWORD=yourpassword
  * sse transport   — each client passes its own SCREENER_USERNAME /
        SCREENER_PASSWORD when it connects to /sse (request headers or query
        params). Credentials are scoped to that connection only.
  Without credentials, public data only (some metrics may be hidden).

Run:
  screener-mcp                          # stdio (default)
  screener-mcp --transport sse          # SSE server on 0.0.0.0:8000
  screener-mcp --transport sse --host 127.0.0.1 --port 9000
"""

import argparse
import base64
import logging
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.staticfiles import StaticFiles

from . import auth as auth_gate
from .client import Credentials, close_all, current_credentials, env_credentials
from .tools.company_tools import (
    search_company as _search_company,
    get_company_overview as _get_overview,
    get_financials as _get_financials,
    get_quarterly_results as _get_quarterly,
    get_shareholding as _get_shareholding,
    get_peers as _get_peers,
    compare_companies as _compare,
)
from .tools.screening_tools import (
    screen_stocks as _screen,
    screen_by_theme as _theme,
    list_themes as _list_themes,
)
from .tools.analysis_tools import (
    get_full_analysis as _full_analysis,
    get_red_flags as _red_flags,
    beginner_explainer as _beginner,
)

logger = logging.getLogger(__name__)

def _safe(result):
    """Wrap a coroutine so network/auth errors become readable messages."""
    import functools
    async def wrapper(*args, **kwargs):
        try:
            return await result(*args, **kwargs)
        except PermissionError as e:
            return (
                f"**Login required.** {e}\n\n"
                "For stdio, set `SCREENER_USERNAME` / `SCREENER_PASSWORD` env vars and restart. "
                "For SSE, reconnect to `/sse` passing those as request headers or query params."
            )
        except httpx.TimeoutException:
            return "**Request timed out.** Screener.in is taking too long to respond — try again in a moment."
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return "**Company not found.** Check the symbol and try `search_company()` to find the correct NSE/BSE code."
            return f"**Screener.in returned an error** ({e.response.status_code}). The site may be down — try again shortly."
        except httpx.NetworkError:
            return "**Cannot reach Screener.in.** Check your internet connection and try again."
        except Exception as e:
            return f"**Unexpected error**: {type(e).__name__}: {e}\n\nIf this persists, please open an issue at https://github.com/LogeshR15/screener-mcp/issues"
    functools.update_wrapper(wrapper, result)
    return wrapper


mcp = FastMCP(
    "Screener Stock Research",
    instructions="""
You are an expert Indian equity analyst with deep knowledge of Indian stock markets,
Screener.in data, and long-term investing principles.

When users ask about stocks, companies, or investment themes:
1. Use the appropriate tools to fetch real Screener.in data
2. Reason over the data like a seasoned analyst
3. Present findings clearly — use tables, bullet points, and plain language
4. Always contextualize numbers (e.g., "ROCE of 25% is excellent — above industry average")
5. Flag important caveats (data is point-in-time, past performance ≠ future results)
6. For beginners, translate jargon into everyday language
7. For advanced users, go deeper into trends and red flags

You have access to all financial statements, ratios, shareholding data, and screening capabilities.
The data comes from Screener.in and covers BSE/NSE listed Indian companies.

Always be honest about limitations: Screener data may lag by a quarter, sector classifications
are broad, and you cannot predict stock prices.
""",
)


# ─── Search & Discovery ────────────────────────────────────────────────────────

@mcp.tool()
async def search_company(query: str) -> str:
    """
    Search for an Indian stock/company by name or NSE/BSE symbol.

    Examples:
      search_company("Tata Consultancy")
      search_company("INFY")
      search_company("Jyothy Labs")
      search_company("ITC")
    """
    return await _safe(_search_company)(query)


@mcp.tool()
async def screen_stocks(query: str, limit: int = 25) -> str:
    """
    Run a custom stock screen using Screener.in query syntax.

    Query field names (exact spelling matters):
      Market Capitalization, Current Price, Price to Earning, Price to book value,
      Return on capital employed, Return on equity, Debt to equity,
      Sales growth 5Years, Sales growth 3Years, Sales growth last year,
      Profit growth 5Years, Profit growth 3Years, Profit growth last year,
      Dividend yield, Pledged percentage, Net cash flow last year,
      Average return on equity 5Years, Average return on capital employed 5Years,
      Current ratio, EV / EBITDA, PEG Ratio

    Operators: > < = AND

    Examples:
      "Market Capitalization < 5000 AND Return on capital employed > 15 AND Debt to equity < 0.5"
      "Profit growth 5Years > 20 AND Sales growth 5Years > 15 AND Debt to equity < 0.3"
      "Dividend yield > 3 AND Debt to equity < 0.5 AND Return on equity > 15"
    """
    return await _safe(_screen)(query, limit=limit)


@mcp.tool()
async def screen_by_theme(theme: str, limit: int = 20) -> str:
    """
    Run a pre-built thematic stock screen.

    Available themes:
      undervalued_small_cap   — Small caps with ROCE > 15%, low debt, PE < 20
      high_roce_low_debt      — ROCE > 20%, debt to equity < 0.3
      compounders             — 15%+ growth across revenue, profit, ROE, ROCE
      turnaround              — Companies with strong recent profit recovery
      rising_profit_falling_price — Profit up, price compressed (potential value)
      improving_roce          — ROCE > 15% with profit momentum
      hidden_gems             — Small cap, high ROCE, strong growth
      dividend_aristocrats    — Consistent dividend payers with strong financials
      qarp                    — Quality at reasonable price
      micro_cap_growth        — High-growth micro caps < ₹1000 Cr
      ev_theme                — EV & auto ancillary growth companies
      chemicals               — Specialty chemicals with strong fundamentals
      defense                 — Defense sector companies with revenue momentum
      railways                — Railway infra/equipment companies
      renewable_energy        — Renewable energy sector growth companies

    Examples:
      screen_by_theme("hidden_gems")
      screen_by_theme("compounders")
      screen_by_theme("chemicals")
    """
    return await _safe(_theme)(theme, limit=limit)


@mcp.tool()
async def list_investment_themes() -> str:
    """
    List all available pre-built investment themes with their screening criteria.
    Use this to discover what thematic screens are available.
    """
    return await _safe(_list_themes)()


# ─── Company Deep Dive ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_company_overview(symbol: str, financial_type: str = "consolidated") -> str:
    """
    Get a company's key ratios, current price, 52-week range, and about section.

    symbol: NSE/BSE symbol (e.g., "TCS", "INFY", "RELIANCE", "HDFCBANK")
    financial_type: "consolidated" (default) or "standalone"

    This is the best starting point for any company analysis.
    """
    return await _safe(_get_overview)(symbol, financial_type)


@mcp.tool()
async def get_financials(
    symbol: str,
    statement: str = "profit_loss",
    financial_type: str = "consolidated",
    years: int = 5,
) -> str:
    """
    Get financial statements for a company.

    symbol: NSE/BSE symbol
    statement options:
      "profit_loss"    — Revenue, expenses, EBITDA, PAT (default)
      "balance_sheet"  — Assets, liabilities, equity, debt
      "cash_flow"      — Operating, investing, financing cash flows
      "ratios"         — Historical PE, PB, ROCE, ROE, etc.
    financial_type: "consolidated" or "standalone"
    years: number of years to show (default 5, max 10)
    """
    valid = {"profit_loss", "balance_sheet", "cash_flow", "ratios"}
    if statement not in valid:
        return f"Invalid statement type '{statement}'. Choose from: {', '.join(valid)}"
    return await _safe(_get_financials)(symbol, statement, financial_type, years)


@mcp.tool()
async def get_quarterly_results(symbol: str, financial_type: str = "consolidated") -> str:
    """
    Get the last 8 quarters of results for a company.

    Shows: Revenue, Expenses, Operating Profit, OPM%, Net Profit, EPS
    Useful for identifying quarter-on-quarter growth trends and seasonality.

    symbol: NSE/BSE symbol (e.g., "TCS", "WIPRO", "MARUTI")
    """
    return await _safe(_get_quarterly)(symbol, financial_type)


@mcp.tool()
async def get_shareholding_pattern(symbol: str) -> str:
    """
    Get shareholding pattern history for a company (last 8 quarters).

    Shows: Promoter, FII, DII, Public holding percentages + pledged %
    Also provides a trend analysis of promoter holding changes.

    Useful for:
      - Detecting promoter confidence (buying/selling)
      - Monitoring FII/DII interest
      - Flagging pledge concerns

    symbol: NSE/BSE symbol
    """
    return await _safe(_get_shareholding)(symbol)


@mcp.tool()
async def get_peer_comparison(symbol: str, financial_type: str = "consolidated") -> str:
    """
    Get peer comparison table as shown on Screener.in for a company.

    Shows the company alongside its sector peers with key metrics
    like Market Cap, Sales, Net Profit, PE, ROCE, etc.

    symbol: NSE/BSE symbol
    """
    return await _safe(_get_peers)(symbol, financial_type)


@mcp.tool()
async def compare_companies(symbols: list[str], financial_type: str = "consolidated") -> str:
    """
    Side-by-side comparison of 2 to 5 companies on all key ratios.

    Fetches data for each company and presents them in a comparative table.
    Best for "ITC vs HUL vs Nestle" type questions.

    symbols: list of NSE/BSE symbols, e.g., ["ITC", "HUL", "NESTLE"]
    financial_type: "consolidated" or "standalone"

    Examples:
      compare_companies(["ITC", "HUL"])
      compare_companies(["TCS", "INFY", "WIPRO", "HCLTECH"])
      compare_companies(["PIDILITIND", "ASIANPAINT", "BERGEPAINT"])
    """
    return await _safe(_compare)(symbols, financial_type)


# ─── Analysis Tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def get_full_analysis(symbol: str, financial_type: str = "consolidated") -> str:
    """
    Fetch ALL financial data for a company in a single call.

    Returns complete data across:
      - Key ratios and overview
      - 10-year P&L, Balance Sheet, Cash Flow
      - 8 quarters of results
      - Historical ratios (PE, ROCE, ROE, etc.)
      - Shareholding pattern
      - Peer comparison

    Use this when you need to do a thorough analysis, identify trends,
    explain a company in depth, or answer complex multi-part questions.

    symbol: NSE/BSE symbol
    """
    return await _safe(_full_analysis)(symbol, financial_type)


@mcp.tool()
async def analyze_red_flags(symbol: str, financial_type: str = "consolidated") -> str:
    """
    Fetch all financial data for a company and generate a structured red flag analysis.

    Systematically checks for:
      - Declining promoter holding or high pledging
      - Rising debt trends
      - Falling ROCE/ROE
      - Cash flow vs profit divergence (profit without cash = concern)
      - Revenue growth without profit growth
      - Rising receivables or inventory vs sales

    Returns data + analysis framework for Claude to identify and explain red flags.

    symbol: NSE/BSE symbol
    """
    return await _safe(_red_flags)(symbol, financial_type)


@mcp.tool()
async def explain_for_beginners(symbol: str) -> str:
    """
    Explain a company in simple, beginner-friendly language.

    Fetches all data and produces a plain-English explanation:
      - What does this company do and how does it make money?
      - Is it profitable and growing?
      - What do the key numbers mean in everyday language?
      - Is the stock expensive or cheap right now?
      - What should a first-time investor watch out for?

    Perfect for: "Explain Jyothy Labs like I'm a beginner"

    symbol: NSE/BSE symbol
    """
    return await _safe(_beginner)(symbol)


# ─── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("screener://analyst-guide")
def analyst_guide() -> str:
    """How to use the Screener Stock Research assistant."""
    return """
# Screener Stock Research — Analyst Guide

## What I can do

I'm your Indian stock research copilot powered by Screener.in data.

### Ask me naturally:
- "Find chemical stocks with low debt and strong growth"
- "Compare ITC and HUL"
- "Explain Jyothy Labs like I'm a beginner"
- "What are the red flags in Asian Paints?"
- "Find hidden gems below ₹5000 crore market cap"
- "Which companies benefit from EV adoption?"
- "Give me businesses with improving ROCE for the last 5 years"
- "Find turnaround stories in mid-cap space"
- "What is Titan's promoter holding trend?"
- "Show me the last 4 quarters for HDFC Bank"

### What I fetch from Screener.in:
- 10+ years of financials (P&L, Balance Sheet, Cash Flow)
- Historical key ratios (PE, ROCE, ROE, Debt/Equity, etc.)
- Quarterly results (last 8 quarters)
- Shareholding patterns (promoter, FII, DII)
- Peer comparison tables
- Stock screening by custom filters or pre-built themes

### Setup for full data:
Set in your environment:
  SCREENER_USERNAME=your@email.com
  SCREENER_PASSWORD=yourpassword

Without login, some data fields may be restricted (Screener.in requires login for full data).

## Limitations
- Data comes from Screener.in and may lag by 1 quarter
- I cannot predict stock prices or guarantee returns
- Always verify critical data directly on Screener.in
- Past financial performance does not guarantee future results
"""


@mcp.resource("screener://query-syntax")
def query_syntax_guide() -> str:
    """Screener.in query language reference for stock screening."""
    return """
# Screener.in Query Language Reference

## Syntax
  FIELD OPERATOR VALUE [AND FIELD OPERATOR VALUE ...]

## Operators
  >   greater than
  <   less than
  =   equals
  AND combine conditions

## Common Fields (exact spelling)
  Market Capitalization          (₹ Crore)
  Current Price
  Price to Earning               (PE ratio)
  Price to book value            (PB ratio)
  EV / EBITDA
  PEG Ratio
  Dividend yield                 (%)
  Return on capital employed     (%)
  Return on equity               (%)
  Debt to equity
  Current ratio
  Pledged percentage             (%)
  Sales growth 5Years            (%)
  Sales growth 3Years            (%)
  Sales growth last year         (%)
  Profit growth 5Years           (%)
  Profit growth 3Years           (%)
  Profit growth last year        (%)
  Average return on equity 5Years
  Average return on capital employed 5Years
  Net cash flow last year

## Example Queries

# Classic GARP (Growth at Reasonable Price)
Price to Earning < 25 AND Profit growth 5Years > 15 AND Return on equity > 15

# Deep Value Small Cap
Market Capitalization < 2000 AND Price to Earning < 15 AND Debt to equity < 0.5

# Quality Compounder
Sales growth 5Years > 15 AND Profit growth 5Years > 15 AND Return on capital employed > 20 AND Debt to equity < 0.3

# High Dividend + Quality
Dividend yield > 3 AND Return on equity > 15 AND Debt to equity < 0.5

# Turnaround Candidate
Profit growth last year > 30 AND Profit growth 3Years > 20 AND Debt to equity < 1

# Momentum + Quality
Profit growth 5Years > 20 AND Sales growth 5Years > 20 AND Return on capital employed > 20
"""


# ─── SSE transport ────────────────────────────────────────────────────────────

CRED_HEADER_USER = "SCREENER_USERNAME"
CRED_HEADER_PASS = "SCREENER_PASSWORD"

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_BANNER_NAMES = ("banner.png", "banner.jpg", "banner.jpeg", "banner.webp", "banner.gif")
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _banner_path() -> Path | None:
    """Resolve the landing-page image: $INDEX_IMAGE first, then a bundled banner."""
    env = os.getenv("INDEX_IMAGE", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        logger.warning("INDEX_IMAGE is set but not found: %s", p)
    for name in _BANNER_NAMES:
        p = _STATIC_DIR / name
        if p.is_file():
            return p
    return None


def _extract_credentials(request) -> Credentials:
    """Pull credentials from a Starlette request: headers take priority, then
    query params. Header names are matched case-insensitively by Starlette."""
    headers = request.headers
    query = request.query_params

    def pick(name: str) -> str:
        value = headers.get(name)
        if value is None:
            value = query.get(name) or query.get(name.lower())
        return (value or "").strip()

    return Credentials(pick(CRED_HEADER_USER), pick(CRED_HEADER_PASS))


def _extract_bearer(request):
    """Return an auth access token if the client presented one, else None.

    Sources: ``Authorization: Bearer <token>``, the ``sso-token`` header, the
    ``X-Access-Token`` header, or an ``access_token`` query param.
    """
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        token = authz[7:].strip()
        if token:
            return token
    token = (
        request.headers.get("sso-token")
        or request.headers.get("X-Access-Token")
        or request.query_params.get("access_token")
    )
    return (token or "").strip() or None


def _extract_login(request):
    """Return (username, password) the client presented for authentication.

    Sources, in order: ``SSO_USERNAME`` / ``SSO_PASSWORD`` headers or query,
    then ``username`` / ``password``, then HTTP Basic ``Authorization``.
    Returns ``None`` if absent.
    """
    headers = request.headers
    query = request.query_params

    def pick(name: str) -> str:
        return (headers.get(name) or query.get(name) or "").strip()

    for user_key, pass_key in (("SSO_USERNAME", "SSO_PASSWORD"), ("username", "password")):
        username = pick(user_key)
        if username:
            return username, pick(pass_key)

    authz = headers.get("Authorization", "")
    if authz.startswith("Basic "):
        try:
            raw = base64.b64decode(authz[6:]).decode("utf-8")
            username, _, password = raw.partition(":")
            if username:
                return username.strip(), password
        except (ValueError, UnicodeDecodeError):
            pass
    return None


def build_sse_app():
    """Build a Starlette app exposing /sse, /messages/ and /health.

    Credentials supplied on the /sse connection are stored in a context variable
    for the lifetime of that session, so every tool call on the connection uses
    them in isolation from other connections.
    """
    from contextlib import asynccontextmanager

    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
    from starlette.routing import Mount, Route

    transport = SseServerTransport("/messages/")
    mcp_server = mcp._mcp_server  # underlying low-level Server

    def _unauthorized(detail: str) -> JSONResponse:
        return JSONResponse(
            {"status": "unauthorized", "detail": detail},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="screener-mcp"'},
        )

    async def handle_sse(request):
        if auth_gate.auth_enabled():
            # Gate: a valid Bearer access token, or username/password, is required.
            bearer = _extract_bearer(request)
            if bearer:
                result = await auth_gate.verify(bearer)
                principal = "bearer-token"
            else:
                login = _extract_login(request)
                if login is None:
                    return _unauthorized("Authentication required.")
                result = await auth_gate.authenticate(*login)
                principal = login[0]
            if not result.ok:
                logger.info("Auth denied (%s): %s", principal, result.status or result.error)
                return _unauthorized(result.status or result.error or "Authentication failed.")
            logger.info("Auth OK (%s, user_id=%s)", principal, result.user_id)
            # Authenticated. Screener account: per-connection headers first,
            # then the server's own .env account.
            creds = _extract_credentials(request)
            source = "connection"
            if creds.is_anonymous:
                creds, source = env_credentials(), "server-env"
            if creds.is_anonymous:
                logger.error(
                    "Authenticated but no Screener credentials supplied "
                    "(connection headers or server .env)."
                )
                return JSONResponse(
                    {
                        "status": "error",
                        "detail": (
                            "Screener username and password are missing. Pass "
                            "SCREENER_USERNAME and SCREENER_PASSWORD as connection "
                            "headers, or set them in the server environment (.env)."
                        ),
                    },
                    status_code=401,
                )
        else:
            creds = _extract_credentials(request)
            source = "connection"
            if creds.is_anonymous:
                env_creds = env_credentials()
                if not env_creds.is_anonymous:
                    creds, source = env_creds, "server-env"

        logger.info("SSE connection opened (%s, creds=%s)", source, creds)
        token = current_credentials.set(creds)
        try:
            async with transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp_server.create_initialization_options(),
                )
        finally:
            current_credentials.reset(token)
        return Response(status_code=204)

    async def health(_request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "screener-mcp",
                "auth": "enabled" if auth_gate.auth_enabled() else "disabled",
            }
        )

    async def index(_request):
        return FileResponse(
            _STATIC_DIR / "index.html",
            media_type="text/html",
        )

    async def banner(_request) -> Response:
        path = _banner_path()
        if path is None:
            return Response(status_code=404)
        media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type)

    async def favicon(_request) -> Response:
        path = _STATIC_DIR / "favicon.ico"
        if path.is_file():
            return FileResponse(path, media_type="image/x-icon")
        return Response(status_code=404)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await close_all()
            await auth_gate.close()

    routes = [
        Route("/", endpoint=index, methods=["GET"]),
        Route("/banner", endpoint=banner, methods=["GET"]),
        Route("/favicon.ico", endpoint=favicon, methods=["GET"]),

        Mount(
            "/static",
            app=StaticFiles(directory=_STATIC_DIR),
            name="static",
        ),

        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=transport.handle_post_message),
        Route("/health", endpoint=health, methods=["GET"]),
    ]

    return Starlette(
        debug=False,
        routes=routes,
        lifespan=lifespan,
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="screener-mcp",
        description="Screener.in MCP server (Indian stock research).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport to run (default: stdio, or $MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host to bind for SSE transport (default: 0.0.0.0, or $HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8000")),
        help="Port to bind for SSE transport (default: 8000, or $PORT).",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level (default: INFO, or $LOG_LEVEL).",
    )
    return parser.parse_args(argv)


def _load_dotenv() -> None:
    """Load a local .env file if python-dotenv is installed (no-op otherwise)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()  # reads ./.env, does not override already-set env vars


def main(argv=None) -> None:
    _load_dotenv()
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.transport == "sse":
        import uvicorn

        logger.info("Starting Screener MCP (SSE) on %s:%s", args.host, args.port)
        uvicorn.run(
            build_sse_app(),
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )
    else:
        logger.info("Starting Screener MCP (stdio)")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
