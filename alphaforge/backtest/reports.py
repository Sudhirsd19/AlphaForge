"""
AlphaForge Quantitative Forensic Report Generator.
Formats deterministic structured text and Markdown reports separating Gross Performance,
Transaction Friction, Net Performance, Risk Metrics, Trade Statistics, Quant Gates,
Overfitting Diagnostics, and Reproducibility Metadata.
"""

from alphaforge.backtest.models import BacktestResult


def generate_backtest_markdown_report(result: BacktestResult) -> str:
    """
    Generate an authoritative, structured Markdown validation report.
    """
    cfg = result.config
    m = result.metrics
    meta = result.dataset_metadata

    lines: list[str] = []
    lines.append(f"# ALPHAFORGE QUANTITATIVE VALIDATION REPORT — {result.backtest_run_id}")
    lines.append("")
    lines.append(f"**Overall Validation Status:** `{result.validation_status.value}`")
    lines.append(f"**Completion Status:** `{result.completion_status}`")
    lines.append("")

    # --- 1. Reproducibility Metadata ---
    lines.append("## 1. Reproducibility & Provenance Metadata")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Backtest Run ID** | `{result.backtest_run_id}` |")
    lines.append(f"| **Strategy ID** | `{cfg.strategy_id}` |")
    lines.append(f"| **Strategy Version** | `{cfg.strategy_version}` |")
    lines.append(f"| **Engine Version** | `{cfg.engine_version}` |")
    lines.append(f"| **Dataset ID** | `{meta.dataset_id}` |")
    lines.append(f"| **Dataset Checksum** | `{meta.checksum[:16]}...` |")
    lines.append(f"| **Record Count** | `{meta.record_count}` |")
    lines.append(
        f"| **Time Horizon** | `{cfg.start_time.isoformat()} to {cfg.end_time.isoformat()}` |"
    )
    lines.append(f"| **Initial Capital** | `{cfg.initial_capital} {cfg.base_currency}` |")
    lines.append(f"| **Final Position Policy** | `{cfg.final_position_policy.value}` |")
    sl_policy = f"STOP_LOSS_FIRST ({cfg.conservative_same_bar_sl_first})"
    lines.append(f"| **OHLC SL/TP Ambiguity Policy** | `{sl_policy}` |")
    lines.append(f"| **Deterministic Seed** | `{cfg.seed}` |")
    lines.append("")

    # --- 2. Financial Performance (Gross vs Friction vs Net) ---
    gross_pct = (m.gross_pnl / cfg.initial_capital * 100) if cfg.initial_capital > 0 else 0
    fees_pct = (m.total_fees / cfg.initial_capital * 100) if cfg.initial_capital > 0 else 0
    slip_pct = (m.total_slippage / cfg.initial_capital * 100) if cfg.initial_capital > 0 else 0
    net_pct = (m.net_pnl / cfg.initial_capital * 100) if cfg.initial_capital > 0 else 0

    lines.append("## 2. Financial Performance (Gross vs Friction vs Net)")
    lines.append("")
    lines.append("| Metric | Monetary Amount | % of Initial Capital |")
    lines.append("| :--- | :--- | :--- |")
    lines.append(f"| **Gross PnL** | `{m.gross_pnl:.2f}` | `{gross_pct:.2f}%` |")
    lines.append(f"| **Transaction Fees** | `-{m.total_fees:.2f}` | `{fees_pct:.2f}%` |")
    lines.append(f"| **Adverse Slippage** | `-{m.total_slippage:.2f}` | `{slip_pct:.2f}%` |")
    lines.append(f"| **Net Realized PnL** | `{m.net_pnl:.2f}` | `{net_pct:.2f}%` |")
    lines.append(f"| **Total Net Return** | `{m.total_return:.2f}` | `{m.total_return_pct:.2f}%` |")
    cagr_str = (
        f"{m.annualized_return_pct:.2f}%"
        if m.annualized_return_pct is not None
        else "INSUFFICIENT_SAMPLE"
    )
    lines.append(f"| **Annualized Return** | `{cagr_str}` | `{cagr_str}` |")
    lines.append("")

    # --- 3. Trade Statistics ---
    lines.append("## 3. Trade Execution Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Total Trades** | `{m.total_trades}` |")
    lines.append(f"| **Winning Trades** | `{m.winning_trades}` |")
    lines.append(f"| **Losing Trades** | `{m.losing_trades}` |")
    lines.append(f"| **Break-Even Trades** | `{m.break_even_trades}` |")
    lines.append(f"| **Win Rate** | `{(m.win_rate * 100):.2f}%` |")
    lines.append(f"| **Loss Rate** | `{(m.loss_rate * 100):.2f}%` |")
    lines.append(f"| **Average Win** | `{m.average_win:.2f}` |")
    lines.append(f"| **Average Loss** | `{m.average_loss:.2f}` |")
    po_str = f"{m.payoff_ratio:.2f}" if m.payoff_ratio is not None else "N/A"
    pf_str = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "N/A"
    lines.append(f"| **Payoff Ratio** | `{po_str}` |")
    lines.append(f"| **Profit Factor** | `{pf_str}` |")
    lines.append(f"| **Expectancy per Trade** | `{m.expectancy:.2f}` |")
    lines.append("")

    # --- 4. Risk & Drawdown Metrics ---
    lines.append("## 4. Risk & Drawdown Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| :--- | :--- |")
    dd_str = f"`{m.max_drawdown:.2f}` (`{(m.max_drawdown_pct * 100):.2f}%`)"
    lines.append(f"| **Max Drawdown** | {dd_str} |")
    lines.append(f"| **Max Drawdown Duration** | `{m.max_drawdown_duration_seconds} seconds` |")
    vol_str = (
        f"{m.volatility_annualized:.2f}%"
        if m.volatility_annualized is not None
        else "INSUFFICIENT_SAMPLE"
    )
    sh_str = f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio is not None else "INSUFFICIENT_SAMPLE"
    so_str = f"{m.sortino_ratio:.2f}" if m.sortino_ratio is not None else "INSUFFICIENT_SAMPLE"
    cal_str = f"{m.calmar_ratio:.2f}" if m.calmar_ratio is not None else "INSUFFICIENT_SAMPLE"
    lines.append(f"| **Annualized Volatility** | `{vol_str}` |")
    lines.append(f"| **Sharpe Ratio** | `{sh_str}` |")
    lines.append(f"| **Sortino Ratio** | `{so_str}` |")
    lines.append(f"| **Calmar Ratio** | `{cal_str}` |")
    lines.append(f"| **Average Exposure** | `{m.average_exposure:.2f}` |")
    lines.append(f"| **Max Exposure** | `{m.max_exposure:.2f}` |")
    lines.append("")

    # --- 5. Quant Gates A-J ---
    lines.append("## 5. Quant Gates A through J Audit")
    lines.append("")
    lines.append("| Gate | Status | Reason |")
    lines.append("| :--- | :--- | :--- |")
    for g in result.quant_gates:
        badge = (
            "PASS"
            if g.status.value == "PASS"
            else ("WARN" if g.status.value == "WARNING" else "FAIL")
        )
        lines.append(f"| **{g.gate_id}: {g.gate_name}** | `{badge}` | {g.reason} |")
    lines.append("")

    # --- 6. Overfitting Diagnostics & Warnings ---
    lines.append("## 6. Overfitting Diagnostics & Telemetry")
    lines.append("")
    for k, v in result.diagnostics.items():
        lines.append(f"- **{k}**: `{v}`")
    lines.append("")

    if result.warnings:
        lines.append("### Methodological Warnings")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    if result.errors:
        lines.append("### Methodological Violations (Failures)")
        for e in result.errors:
            lines.append(f"- ❌ {e}")
        lines.append("")

    return "\n".join(lines)


def generate_backtest_text_report(result: BacktestResult) -> str:
    """
    Generate an ASCII plain text summary report.
    """
    md = generate_backtest_markdown_report(result)
    return md.replace("**", "").replace("`", "").replace("|", " ")
