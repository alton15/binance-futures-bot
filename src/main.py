"""CLI entry point - futuresbot commands."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _resolve_profiles(args: argparse.Namespace) -> list[str]:
    """Resolve --profile arg to list of profile names."""
    profile_val = getattr(args, "profile", None)
    if profile_val == "all" or getattr(args, "multi", False):
        return ["conservative", "neutral", "aggressive"]
    if profile_val and profile_val != "all":
        return [profile_val]
    return ["neutral"]


async def cmd_run(args: argparse.Namespace) -> None:
    """Run the full trading pipeline once or start scheduler."""
    import os

    if args.paper:
        os.environ["TRADING_MODE"] = "paper"
    elif args.live:
        os.environ["TRADING_MODE"] = "live"

    from config.profiles import get_profile, ALL_PROFILES
    from src.strategy.orchestrator import run_pipeline, run_multi_profile_pipeline

    profiles = _resolve_profiles(args)

    if args.loop:
        from scripts.scheduler import _run_scheduler
        await _run_scheduler()
    elif len(profiles) > 1:
        profile_objs = [get_profile(p) for p in profiles]
        results = await run_multi_profile_pipeline(
            dry_run=args.dry_run, profiles=profile_objs,
        )
        print(f"\nMulti-Profile Pipeline Results:")
        for r in results:
            status = "SUCCESS" if r.success else "FAILED"
            print(f"\n  [{r.profile_name.capitalize()}] {status}")
            print(f"    Scanned:  {r.coins_scanned}")
            print(f"    Analyzed: {r.coins_analyzed}")
            print(f"    Traded:   {r.trades_executed}")
            print(f"    Skipped:  {r.trades_skipped}")
            if r.error:
                print(f"    Error:    {r.error}")
    else:
        profile = get_profile(profiles[0])
        result = await run_pipeline(dry_run=args.dry_run, profile=profile)
        print(f"\nPipeline [{profile.label}] {'SUCCESS' if result.success else 'FAILED'}")
        print(f"  Scanned:  {result.coins_scanned}")
        print(f"  Analyzed: {result.coins_analyzed}")
        print(f"  Traded:   {result.trades_executed}")
        print(f"  Skipped:  {result.trades_skipped}")
        if result.error:
            print(f"  Error:    {result.error}")


async def cmd_scan(args: argparse.Namespace) -> None:
    """Scan coins without trading."""
    from src.clients.binance_rest import BinanceClient
    from src.scanner.coin_scanner import scan_coins
    from src.db.models import init_db

    await init_db()
    async with BinanceClient() as client:
        candidates = await scan_coins(client, max_candidates=args.limit)

    print(f"\n{'='*80}")
    print(f"Found {len(candidates)} candidate coins")
    print(f"{'='*80}\n")

    for i, c in enumerate(candidates, 1):
        print(f"{i}. {c.symbol}")
        print(f"   Vol24h=${c.volume_24h:,.0f}  Volatility={c.volatility_24h:.2%}  "
              f"Spread={c.spread:.4%}  FundingRate={c.funding_rate:.4%}")
        print(f"   Score={c.scan_score:.4f}")
        print()


async def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze a specific symbol."""
    from src.clients.binance_rest import BinanceClient
    from src.strategy.analyzer import analyze_coin
    from src.db.models import init_db

    await init_db()
    symbol = args.symbol.upper()
    if "/" not in symbol:
        symbol = f"{symbol}/USDT:USDT"

    async with BinanceClient() as client:
        result = await analyze_coin(client, symbol)

    if not result:
        print(f"Analysis failed for {symbol}")
        return

    print(f"\n{'='*60}")
    print(f"  Analysis: {result['symbol']}")
    print(f"{'='*60}")
    print(f"  Direction:  {result['direction']}")
    print(f"  Strength:   {result['strength']:.2f}")
    print(f"  Confirming: {result['confirming_count']}")
    print(f"  Close:      ${result['close_price']:.4f}")
    print(f"  ATR:        ${result['atr']:.4f}")
    if result.get("details"):
        print(f"\n  Indicator Votes:")
        for name, vote in result["details"].items():
            print(f"    {name:15s} {vote['direction']:6s} (weight={vote['weight']:.1f})")
    print()


async def cmd_status(args: argparse.Namespace) -> None:
    """Show current bot status and open positions."""
    from config.settings import TRADING_MODE, INITIAL_CAPITAL
    from src.db.models import init_db, get_open_positions, get_trading_stats

    await init_db()
    is_paper = TRADING_MODE == "paper"
    profiles = _resolve_profiles(args)

    for profile_name in profiles:
        positions = await get_open_positions(is_paper=is_paper, profile=profile_name)
        stats = await get_trading_stats(is_paper=is_paper, profile=profile_name)

        mode = "PAPER" if is_paper else "LIVE"
        print(f"\n{'='*60}")
        print(f"  Futures Bot Status [{mode}/{profile_name.capitalize()}]")
        print(f"{'='*60}")
        print(f"  Capital:          ${INITIAL_CAPITAL:.2f}")
        print(f"  Total Trades:     {stats['total_trades']}")
        print(f"  Open Positions:   {stats['open_positions']}")
        print(f"  Closed:           {stats['closed_positions']}")
        print(f"  Win Rate:         {stats['win_rate']:.1%}")
        print(f"  Total P&L:        ${stats['total_realized_pnl']:.4f}")
        print(f"  Unrealized P&L:   ${stats['unrealized_pnl']:.4f}")
        print(f"  Margin In Use:    ${stats['total_margin_in_use']:.4f}")
        print(f"  Funding Paid:     ${stats['total_funding_paid']:.4f}")

        if positions:
            print(f"\n  Open Positions:")
            for p in positions:
                pnl = p.get("unrealized_pnl", 0)
                sign = "+" if pnl >= 0 else ""
                lev = p.get("leverage", 1)
                print(f"    - {p['symbol']:15s} {p['direction']:5s} {lev}x "
                      f"@ {p['entry_price']:.4f} ({sign}${pnl:.4f})")
    print()


async def cmd_positions(args: argparse.Namespace) -> None:
    """Show detailed position info."""
    from config.settings import TRADING_MODE
    from src.db.models import init_db, get_open_positions

    await init_db()
    is_paper = TRADING_MODE == "paper"
    profiles = _resolve_profiles(args)

    for profile_name in profiles:
        positions = await get_open_positions(is_paper=is_paper, profile=profile_name)

        if not positions:
            print(f"\nNo open positions [{profile_name.capitalize()}].")
            continue

        mode = "PAPER" if is_paper else "LIVE"
        print(f"\n{'='*80}")
        print(f"  Open Positions [{mode}/{profile_name.capitalize()}]")
        print(f"{'='*80}\n")

        for p in positions:
            pnl = p.get("unrealized_pnl", 0)
            sign = "+" if pnl >= 0 else ""
            print(f"  {p['symbol']} | {p['direction']} {p['leverage']}x")
            print(f"    Entry: ${p['entry_price']:.4f}  Current: ${p.get('current_price', 0):.4f}")
            print(f"    Size: {p['size']:.4f}  Margin: ${p['margin']:.4f}")
            print(f"    PnL: {sign}${pnl:.4f}  Funding: ${p.get('funding_paid', 0):.4f}")
            print(f"    SL: ${p.get('sl_price', 0):.4f}  TP: ${p.get('tp_price', 0):.4f}")
            print(f"    Liquidation: ${p.get('liquidation_price', 0):.4f}")
            print(f"    Opened: {p['opened_at']}")
            print()


async def cmd_history(args: argparse.Namespace) -> None:
    """Show recent trade history."""
    from config.settings import TRADING_MODE
    from src.db.models import init_db, get_recent_trades

    await init_db()
    is_paper = TRADING_MODE == "paper"
    profiles = _resolve_profiles(args)

    for profile_name in profiles:
        trades = await get_recent_trades(is_paper=is_paper, profile=profile_name, limit=args.limit)

        if not trades:
            print(f"\nNo trades yet [{profile_name.capitalize()}].")
            continue

        print(f"\n{'='*80}")
        print(f"  Recent Trades [{profile_name.capitalize()}] (last {args.limit})")
        print(f"{'='*80}\n")

        for t in trades:
            rpnl = t.get("realized_pnl")
            pnl_str = f"${rpnl:.4f}" if rpnl is not None else "open"
            print(f"  [{t['created_at']}] {t['direction']:5s} {t['symbol']}")
            print(f"    Size={t['size']:.4f}  Entry=${t['entry_price']:.4f}  "
                  f"Leverage={t['leverage']}x  P&L={pnl_str}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="futuresbot",
        description="Binance Futures Trading Bot",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    p_run = subparsers.add_parser("run", help="Run trading pipeline")
    p_run.add_argument("--paper", action="store_true", help="Force paper trading mode")
    p_run.add_argument("--live", action="store_true", help="Force live trading mode")
    p_run.add_argument("--dry-run", action="store_true", help="Analyze without trading")
    p_run.add_argument("--loop", action="store_true", help="Start scheduler daemon")
    p_run.add_argument(
        "--profile", choices=["conservative", "neutral", "aggressive", "all"],
        default=None, help="Trading profile (default: neutral, --loop: all)",
    )
    p_run.add_argument("--multi", action="store_true", help="Run all 3 profiles (= --profile all)")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan coins")
    p_scan.add_argument("--limit", type=int, default=10, help="Max candidates")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze a specific symbol")
    p_analyze.add_argument("symbol", help="Trading pair (e.g. BTCUSDT or BTC/USDT:USDT)")

    # status
    p_status = subparsers.add_parser("status", help="Show bot status")
    p_status.add_argument(
        "--profile", choices=["conservative", "neutral", "aggressive", "all"],
        default="neutral", help="Profile to show (default: neutral)",
    )

    # positions
    p_positions = subparsers.add_parser("positions", help="Show open positions detail")
    p_positions.add_argument(
        "--profile", choices=["conservative", "neutral", "aggressive", "all"],
        default="neutral", help="Profile to show (default: neutral)",
    )

    # history
    p_history = subparsers.add_parser("history", help="Show trade history")
    p_history.add_argument("--limit", type=int, default=20, help="Number of trades")
    p_history.add_argument(
        "--profile", choices=["conservative", "neutral", "aggressive", "all"],
        default="neutral", help="Profile to show (default: neutral)",
    )

    # backtest
    p_bt = subparsers.add_parser("backtest", help="Run backtest")
    p_bt.add_argument("symbol", help="Trading pair")
    p_bt.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    p_bt.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return

    cmd_map = {
        "run": cmd_run,
        "scan": cmd_scan,
        "analyze": cmd_analyze,
        "status": cmd_status,
        "positions": cmd_positions,
        "history": cmd_history,
        "backtest": lambda a: _cmd_backtest(a),
    }

    asyncio.run(cmd_map[args.command](args))


async def _cmd_backtest(args: argparse.Namespace) -> None:
    """Run backtest."""
    from scripts.backtest import run_backtest
    symbol = args.symbol.upper()
    if "/" not in symbol:
        symbol = f"{symbol}/USDT:USDT"
    await run_backtest(symbol, args.from_date, args.to_date)


if __name__ == "__main__":
    main()
