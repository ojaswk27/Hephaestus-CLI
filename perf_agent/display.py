"""All Rich UI: spinner, metrics table, streaming LLM panel, errors."""

from __future__ import annotations

import difflib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .parser import HotFunction, StatMetrics

if TYPE_CHECKING:
    from .compiler import CompileResult
    from .optimizer import IterationRecord

CONSOLE = Console()


def show_banner(binary: str) -> None:
    CONSOLE.print(
        Panel(
            f"[bold cyan]perf-agent[/]  [dim]AI-powered Linux profiler[/]\n"
            f"[green]Target:[/] {binary}",
            border_style="cyan",
            expand=False,
        )
    )


@contextmanager
def spinner(message: str) -> Iterator[None]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=CONSOLE,
        transient=True,
    ) as progress:
        progress.add_task(message, total=None)
        yield


def _ipc_color(ipc: float | None) -> str:
    if ipc is None:
        return "white"
    if ipc >= 2.0:
        return "green"
    if ipc >= 1.0:
        return "yellow"
    return "red"


def _pct_color(pct: float | None, warn: float, bad: float) -> str:
    if pct is None:
        return "white"
    if pct >= bad:
        return "red"
    if pct >= warn:
        return "yellow"
    return "green"


def show_metrics_table(metrics: StatMetrics, functions: list[HotFunction]) -> None:
    # --- Hardware counters table ---
    hw_table = Table(show_header=True, header_style="bold magenta", expand=False)
    hw_table.add_column("Metric", style="cyan", min_width=22)
    hw_table.add_column("Value", justify="right", min_width=18)
    hw_table.add_column("Notes", style="dim")

    def _fmt_int(v: int | None) -> str:
        return f"{v:,}" if v is not None else "[dim]N/A[/]"

    def _fmt_float(v: float | None, precision: int = 2, suffix: str = "") -> str:
        return f"{v:.{precision}f}{suffix}" if v is not None else "[dim]N/A[/]"

    ipc_color = _ipc_color(metrics.ipc)
    hw_table.add_row(
        "IPC (instructions/cycle)",
        f"[{ipc_color}]{_fmt_float(metrics.ipc)}[/]",
        ">=2 good, <1 poor",
    )
    hw_table.add_row("Total Cycles", _fmt_int(metrics.cycles), "")
    hw_table.add_row("Total Instructions", _fmt_int(metrics.instructions), "")
    hw_table.add_row("Task Clock", _fmt_float(metrics.task_clock_ms, 1, " ms"), "")
    hw_table.add_row("CPUs Utilized", _fmt_float(metrics.cpu_utilized), "")
    hw_table.add_row("Elapsed Time", _fmt_float(metrics.elapsed_seconds, 3, " s"), "")

    bm_color = _pct_color(metrics.branch_miss_pct, 1.0, 3.0)
    hw_table.add_row(
        "Branch Misses",
        f"[{bm_color}]{_fmt_float(metrics.branch_miss_pct, 1, '%')}[/]",
        f"of {_fmt_int(metrics.branches)} branches",
    )

    cm_color = _pct_color(metrics.cache_miss_pct, 3.0, 10.0)
    hw_table.add_row(
        "Cache Misses",
        f"[{cm_color}]{_fmt_float(metrics.cache_miss_pct, 1, '%')}[/]",
        f"of {_fmt_int(metrics.cache_references)} cache refs",
    )

    CONSOLE.print(Panel(hw_table, title="[bold cyan]Hardware Counters[/]", border_style="cyan"))

    # --- Hot functions table ---
    if functions:
        fn_table = Table(show_header=True, header_style="bold magenta", expand=False)
        fn_table.add_column("%", justify="right", style="yellow", min_width=7)
        fn_table.add_column("Samples", justify="right", min_width=8)
        fn_table.add_column("Symbol", style="bold white")
        fn_table.add_column("DSO", style="dim")

        for f in functions[:10]:
            fn_table.add_row(
                f"{f.overhead_pct:.2f}%",
                str(f.samples),
                f.symbol,
                f.dso,
            )

        CONSOLE.print(Panel(fn_table, title="[bold cyan]Top Hot Functions[/]", border_style="cyan"))


def show_warning_no_symbols() -> None:
    CONSOLE.print(
        Panel(
            "[yellow]Warning: Debug symbols missing.[/]\n\n"
            "The perf report shows mostly \\[unknown] frames.\n"
            "Recompile with debug symbols for meaningful profiling:\n\n"
            "  [cyan]gcc -O2 -g -fno-omit-frame-pointer -o mybinary mysource.c[/]\n"
            "  [cyan]cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo ..[/]",
            title="[bold yellow]No Debug Symbols[/]",
            border_style="yellow",
        )
    )


def stream_llm_panel(chunks: Iterator[tuple[str, bool]]) -> None:
    """Stream LLM output. Chunks are (text, is_thinking) tuples.

    Thinking content is shown in a dim panel above the main analysis panel.
    """
    thinking_buffer = ""
    analysis_buffer = ""

    def _renderable() -> Group:
        panels: list[Panel] = []
        if thinking_buffer:
            panels.append(
                Panel(
                    Markdown(thinking_buffer),
                    title="[dim]Model Thinking[/]",
                    border_style="dim",
                )
            )
        panels.append(
            Panel(
                Markdown(analysis_buffer) if analysis_buffer else "",
                title="[bold green]AI Analysis[/]",
                border_style="green",
            )
        )
        return Group(*panels)

    with Live(
        _renderable(),
        console=CONSOLE,
        refresh_per_second=10,
        vertical_overflow="visible",
    ) as live:
        for chunk, is_thinking in chunks:
            if is_thinking:
                thinking_buffer += chunk
            else:
                analysis_buffer += chunk
            live.update(_renderable())


def show_error(msg: str) -> None:
    CONSOLE.print(
        Panel(
            f"[bold red]Error:[/] {msg}",
            title="[bold red]perf-agent error[/]",
            border_style="red",
        )
    )


# ---------------------------------------------------------------------------
# Optimizer-specific display functions
# ---------------------------------------------------------------------------


def show_iteration_header(iteration: int, max_iterations: int) -> None:
    CONSOLE.print(
        Rule(
            f"[bold blue]Iteration {iteration} / {max_iterations}[/]",
            style="blue",
        )
    )


def show_compile_result(result: CompileResult) -> None:
    if result.success:
        CONSOLE.print(
            Panel(
                f"[green]Compiled successfully[/] in {result.elapsed_seconds:.1f}s",
                border_style="green",
                expand=False,
            )
        )
    else:
        stderr_lines = result.stderr.splitlines()[:40]
        stderr_text = "\n".join(stderr_lines)
        if len(result.stderr.splitlines()) > 40:
            stderr_text += "\n... (truncated)"
        CONSOLE.print(
            Panel(
                f"[bold red]Compilation failed[/]\n\n{stderr_text}",
                title="[bold red]Compile Error[/]",
                border_style="red",
            )
        )


def show_llm_thinking(thinking_text: str, iteration: int) -> None:
    if not thinking_text:
        return
    CONSOLE.print(
        Panel(
            Markdown(thinking_text),
            title=f"[dim]Model Thinking — Iteration {iteration}[/]",
            border_style="dim",
        )
    )


def show_llm_optimization_response(response_text: str, iteration: int) -> None:
    import re
    # Strip any fenced code block — show only the surrounding explanation
    _CODE_BLOCK_RE = re.compile(r"```\w*\s*\n.*?```", re.DOTALL)
    display_text = _CODE_BLOCK_RE.sub("*(source code block omitted)*", response_text).strip()
    CONSOLE.print(
        Panel(
            Markdown(display_text),
            title=f"[bold blue]AI Optimization Proposal — Iteration {iteration}[/]",
            border_style="blue",
        )
    )


def show_iteration_result(record: IterationRecord) -> None:
    def _fmt(v: float | None) -> str:
        return f"{v:.3f}s" if v is not None else "N/A"

    def _fmt_ipc(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "N/A"

    if record.compile_failed:
        status = "[bold red]COMPILE FAILED[/]"
        border = "red"
        detail = f"  {record.description}"
    elif record.kept:
        status = "[bold green]KEPT[/]"
        border = "green"
        detail = (
            f"  {record.description}\n"
            f"  elapsed: {_fmt(record.elapsed_before)} → {_fmt(record.elapsed_after)}"
            f"  ({record.delta_pct:+.1f}%)"
            f"   IPC: {_fmt_ipc(record.ipc_before)} → {_fmt_ipc(record.ipc_after)}"
        )
    else:
        status = "[bold yellow]REJECTED[/]"
        border = "yellow"
        reason = f"  ({record.revert_reason})" if record.revert_reason else ""
        detail = (
            f"  {record.description}\n"
            f"  elapsed: {_fmt(record.elapsed_before)} → {_fmt(record.elapsed_after)}"
            f"  ({record.delta_pct:+.1f}%){reason}"
        )

    CONSOLE.print(
        Panel(
            f"{status}\n{detail}",
            title=f"[dim]Iteration {record.iteration} result[/]",
            border_style=border,
            expand=False,
        )
    )


def prompt_user_approval(
    current_source: str,
    proposed_source: str,
    source_name: str = "source",
) -> tuple[bool, str]:
    """Show proposed diff and prompt the user to approve or reject.

    Returns (approved, feedback).  feedback is non-empty only on rejection with text.
    """
    diff_lines = list(
        difflib.unified_diff(
            current_source.splitlines(keepends=True),
            proposed_source.splitlines(keepends=True),
            fromfile=f"a/{source_name}",
            tofile=f"b/{source_name}",
        )
    )
    if diff_lines:
        diff_text = "".join(diff_lines)
        CONSOLE.print(Panel(
            Syntax(diff_text, "diff", theme="monokai", word_wrap=True),
            title="[bold]Proposed Change[/]",
            border_style="yellow",
        ))

    answer = CONSOLE.input(
        "[bold yellow]Accept this change?[/] [dim][y / n / feedback message][/] "
    ).strip()

    if answer.lower() in ("y", "yes", ""):
        return True, ""
    return False, answer if answer.lower() not in ("n", "no") else ""


def show_source_diff(old_source: str, new_source: str, path: Path) -> None:
    """Display a unified diff of the source change that was just KEPT."""
    diff_lines = list(
        difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="",
        )
    )
    if not diff_lines:
        return

    # Colour the diff manually: +/- lines get green/red
    text = Text()
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            text.append(line + "\n", style="bold")
        elif line.startswith("+"):
            text.append(line + "\n", style="green")
        elif line.startswith("-"):
            text.append(line + "\n", style="red")
        elif line.startswith("@@"):
            text.append(line + "\n", style="cyan")
        else:
            text.append(line + "\n", style="dim")

    CONSOLE.print(
        Panel(
            text,
            title=f"[bold green]Source Change — {path.name}[/]",
            border_style="green",
        )
    )


def show_security_report(report) -> None:
    """Display per-layer security check results."""
    from .security import SecurityReport

    border = "green" if report.passed else "red"
    rows: list[str] = []
    for lr in report.layers:
        if "skipped" in lr.layer:
            status = "[dim](skipped)[/]"
        elif lr.passed:
            status = "[green]✓ passed[/]"
        else:
            status = "[bold red]✗ FAILED[/]"
            if lr.issues:
                status += "  " + lr.issues[0][:60]

        rows.append(f"  {lr.layer:<18} {status}")

    body = "\n".join(rows)

    # Show any raw output for failed layers, dimmed
    extras: list[str] = []
    for lr in report.layers:
        if not lr.passed and lr.raw_output:
            extras.append(f"[dim]{lr.layer} output (truncated):\n{lr.raw_output[:500]}[/]")
    if extras:
        body += "\n\n" + "\n\n".join(extras)

    CONSOLE.print(
        Panel(
            body,
            title="[bold]Security Check[/]",
            border_style=border,
            expand=False,
        )
    )


def show_security_remediation(issues: list[str], accepted: bool) -> None:
    """Display result of the LLM security remediation pre-pass."""
    if accepted:
        body = (
            "[bold green]Security issues remediated by LLM — fixed source accepted.[/]\n"
            + "\n".join(f"  [green]✓[/] {issue}" for issue in issues)
        )
        border = "green"
        title = "Security Remediation — Accepted"
    else:
        body = (
            "[yellow]LLM remediation did not produce a clean fix — "
            "proceeding with original source.[/]\n"
            + "\n".join(f"  [red]✗[/] {issue}" for issue in issues)
        )
        border = "yellow"
        title = "Security Remediation — Skipped"
    CONSOLE.print(Panel(body, title=f"[bold]{title}[/]", border_style=border, expand=False))


def show_check_result(passed: bool, reason: str) -> None:
    """Display the outcome of the correctness gate."""
    if passed:
        CONSOLE.print("  [green]✓ Correctness check passed[/]")
    else:
        body = f"[bold red]✗ Output correctness check failed[/]"
        if reason:
            body += f"\n[dim]{reason[:400]}[/]"
        CONSOLE.print(Panel(body, title="[bold red]Correctness Gate[/]",
                            border_style="red", expand=False))


def show_near_theoretical_best(theoretical_score: float) -> None:
    CONSOLE.print(
        Panel(
            f"[bold green]Within 5% of theoretical best performance[/]\n"
            f"Theoretical best score: [cyan]{theoretical_score:.4f}[/]  "
            f"(lower = better; baseline = 1.0)\n"
            f"Further optimization is unlikely to yield meaningful gains.",
            title="[bold green]Optimization Ceiling Reached[/]",
            border_style="green",
        )
    )


def show_optimization_summary(history: list[IterationRecord], source: Path) -> None:
    CONSOLE.print(Rule("[bold cyan]Optimization Summary[/]", style="cyan"))

    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("#", justify="right", style="dim", min_width=3)
    table.add_column("Result", min_width=14)
    table.add_column("Before", justify="right", min_width=8)
    table.add_column("After", justify="right", min_width=8)
    table.add_column("Delta", justify="right", min_width=7)
    table.add_column("Description")

    def _fmt(v: float | None) -> str:
        return f"{v:.3f}s" if v is not None else "N/A"

    any_kept = False
    for rec in history:
        if rec.compile_failed:
            result_str = "[red]COMPILE FAIL[/]"
            before_str = after_str = delta_str = "[dim]N/A[/]"
        elif rec.correctness_check_failed:
            result_str = "[red]WRONG OUTPUT[/]"
            before_str = after_str = delta_str = "[dim]N/A[/]"
        elif rec.kept:
            result_str = "[green]KEPT[/]"
            before_str = _fmt(rec.elapsed_before)
            after_str = _fmt(rec.elapsed_after)
            delta_str = f"[green]{rec.delta_pct:+.1f}%[/]"
            any_kept = True
        else:
            result_str = "[yellow]REJECTED[/]"
            before_str = _fmt(rec.elapsed_before)
            after_str = _fmt(rec.elapsed_after)
            delta_str = f"[yellow]{rec.delta_pct:+.1f}%[/]"

        table.add_row(
            str(rec.iteration),
            result_str,
            before_str,
            after_str,
            delta_str,
            rec.description[:60],
        )

    CONSOLE.print(table)

    # Footer
    kept = [r for r in history if r.kept]
    if kept and kept[0].elapsed_before and kept[-1].elapsed_after:
        first_before = kept[0].elapsed_before
        last_after = kept[-1].elapsed_after
        total_pct = (last_after - first_before) / first_before * 100
        CONSOLE.print(
            f"\n[bold]Total improvement:[/] [green]{total_pct:+.1f}%[/]"
            f" ({first_before:.3f}s → {last_after:.3f}s)"
        )
    elif not any_kept:
        CONSOLE.print("\n[dim]No improvements found.[/]")

    if any_kept:
        CONSOLE.print(f"[dim]Optimized source written to:[/] {source}")
