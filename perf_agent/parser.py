"""Parse perf text output into structured dataclasses."""

import re
from dataclasses import dataclass, field


@dataclass
class StatMetrics:
    task_clock_ms: float | None = None
    cycles: int | None = None
    instructions: int | None = None
    ipc: float | None = None
    branches: int | None = None
    branch_misses: int | None = None
    branch_miss_pct: float | None = None
    cache_references: int | None = None
    cache_misses: int | None = None
    cache_miss_pct: float | None = None
    elapsed_seconds: float | None = None
    cpu_utilized: float | None = None


@dataclass
class HotFunction:
    overhead_pct: float
    samples: int
    symbol: str
    dso: str


# ---------------------------------------------------------------------------
# Regex patterns for perf stat -d output
# ---------------------------------------------------------------------------

_INT = r"[\d,.]+"  # matches numbers with commas (e.g. 1,234,567)

_PATTERNS: dict[str, re.Pattern[str]] = {
    "task_clock": re.compile(
        r"^\s+([\d,.]+)\s+(?:msec\s+)?task-clock", re.MULTILINE
    ),
    "cycles": re.compile(r"^\s+([\d,]+)\s+cpu-cycles", re.MULTILINE),
    # IPC may be in the annotations OR computed from cycles/instructions
    "instructions_ipc": re.compile(
        r"^\s+([\d,]+)\s+instructions.*?#\s+([\d.]+)\s+insn per cycle",
        re.MULTILINE,
    ),
    "instructions": re.compile(r"^\s+([\d,]+)\s+instructions", re.MULTILINE),
    "branches": re.compile(r"^\s+([\d,]+)\s+branches(?:\s|$)", re.MULTILINE),
    "branch_misses_pct": re.compile(
        r"^\s+([\d,]+)\s+branch-misses.*?#\s+([\d.]+)%\s+of all branches",
        re.MULTILINE,
    ),
    "branch_misses": re.compile(r"^\s+([\d,]+)\s+branch-misses", re.MULTILINE),
    "cache_references": re.compile(
        r"^\s+([\d,]+)\s+cache-references", re.MULTILINE
    ),
    "cache_misses_pct": re.compile(
        r"^\s+([\d,]+)\s+cache-misses.*?#\s+([\d.]+)%\s+of all cache refs",
        re.MULTILINE,
    ),
    "cache_misses": re.compile(r"^\s+([\d,]+)\s+cache-misses", re.MULTILINE),
    "elapsed": re.compile(
        r"([\d.]+)\s+seconds time elapsed", re.MULTILINE
    ),
    "cpu_util": re.compile(
        r"([\d.]+)\s+CPUs utilized", re.MULTILINE
    ),
}


def _strip_commas(s: str) -> str:
    return s.replace(",", "")


def parse_stat(raw: str) -> StatMetrics:
    m = StatMetrics()

    hit = _PATTERNS["task_clock"].search(raw)
    if hit:
        m.task_clock_ms = float(_strip_commas(hit.group(1)))

    hit = _PATTERNS["cycles"].search(raw)
    if hit:
        m.cycles = int(_strip_commas(hit.group(1)))

    # Try annotated IPC first (some perf versions emit it), then fall back
    hit = _PATTERNS["instructions_ipc"].search(raw)
    if hit:
        m.instructions = int(_strip_commas(hit.group(1)))
        m.ipc = float(hit.group(2))
    else:
        hit = _PATTERNS["instructions"].search(raw)
        if hit:
            m.instructions = int(_strip_commas(hit.group(1)))

    # Compute IPC from raw counters if not already set
    if m.ipc is None and m.instructions and m.cycles:
        m.ipc = round(m.instructions / m.cycles, 2)

    hit = _PATTERNS["branches"].search(raw)
    if hit:
        m.branches = int(_strip_commas(hit.group(1)))

    hit = _PATTERNS["branch_misses_pct"].search(raw)
    if hit:
        m.branch_misses = int(_strip_commas(hit.group(1)))
        m.branch_miss_pct = float(hit.group(2))
    else:
        hit = _PATTERNS["branch_misses"].search(raw)
        if hit:
            m.branch_misses = int(_strip_commas(hit.group(1)))

    # Compute branch miss % if not annotated
    if m.branch_miss_pct is None and m.branch_misses and m.branches:
        m.branch_miss_pct = round(m.branch_misses / m.branches * 100, 2)

    hit = _PATTERNS["cache_references"].search(raw)
    if hit:
        m.cache_references = int(_strip_commas(hit.group(1)))

    hit = _PATTERNS["cache_misses_pct"].search(raw)
    if hit:
        m.cache_misses = int(_strip_commas(hit.group(1)))
        m.cache_miss_pct = float(hit.group(2))
    else:
        hit = _PATTERNS["cache_misses"].search(raw)
        if hit:
            m.cache_misses = int(_strip_commas(hit.group(1)))

    # Compute cache miss % if not annotated
    if m.cache_miss_pct is None and m.cache_misses and m.cache_references:
        m.cache_miss_pct = round(m.cache_misses / m.cache_references * 100, 2)

    hit = _PATTERNS["elapsed"].search(raw)
    if hit:
        m.elapsed_seconds = float(hit.group(1))

    hit = _PATTERNS["cpu_util"].search(raw)
    if hit:
        m.cpu_utilized = float(hit.group(1))

    return m


# ---------------------------------------------------------------------------
# perf report parser
# ---------------------------------------------------------------------------

# Matches lines like (perf report --stdio --no-children -n output):
#   overhead  samples  command  shared_object  [type] symbol
#    99.57%        2   binary   binary         [.] main
#     0.21%        1   binary   [kernel.kallsyms]  [k] some_kfunc
_REPORT_FULL = re.compile(
    r"^\s*(\d+\.\d+)%\s+(\d+)\s+(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+(\S+)"
)


def parse_report(raw: str) -> list[HotFunction]:
    functions: list[HotFunction] = []
    for line in raw.splitlines():
        m = _REPORT_FULL.match(line)
        if m:
            functions.append(
                HotFunction(
                    overhead_pct=float(m.group(1)),
                    samples=int(m.group(2)),
                    symbol=m.group(6),
                    dso=m.group(4),
                )
            )

    # Sort by overhead descending and return top 20
    functions.sort(key=lambda f: f.overhead_pct, reverse=True)
    return functions[:20]


def has_symbols(functions: list[HotFunction]) -> bool:
    """Return False if more than 50% of overhead is in [unknown] symbols."""
    if not functions:
        return False
    unknown_pct = sum(
        f.overhead_pct for f in functions if f.symbol in ("[unknown]", "")
    )
    total_pct = sum(f.overhead_pct for f in functions)
    if total_pct == 0:
        return False
    return (unknown_pct / total_pct) < 0.5
