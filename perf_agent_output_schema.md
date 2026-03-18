# perf-agent — Output Schema Reference

This document describes every data structure perf-agent produces. All fields map
directly to Python dataclasses; the JSON shapes below are what you get if you
serialize them with `dataclasses.asdict()`.

---

## Mode 1 — Analysis only (`--loops 0`)

A single profile run produces two objects plus a free-text LLM string.

### `StatMetrics`

Hardware counters parsed from `perf stat` output.

```json
{
  "task_clock_ms": 1342.7,
  "cycles": 4821903412,
  "instructions": 9134200881,
  "ipc": 1.89,
  "branches": 1203480912,
  "branch_misses": 14201034,
  "branch_miss_pct": 1.18,
  "cache_references": 38201034,
  "cache_misses": 1240300,
  "cache_miss_pct": 3.25,
  "elapsed_seconds": 1.344,
  "cpu_utilized": 0.99
}
```

| Field | Type | Notes |
|---|---|---|
| `task_clock_ms` | `float \| null` | CPU time charged to the task (ms) |
| `cycles` | `int \| null` | Raw CPU cycle count |
| `instructions` | `int \| null` | Retired instruction count |
| `ipc` | `float \| null` | Instructions per cycle; ≥2 good, <1 poor |
| `branches` | `int \| null` | Total branch instructions |
| `branch_misses` | `int \| null` | Branch mispredictions (absolute) |
| `branch_miss_pct` | `float \| null` | `branch_misses / branches × 100` |
| `cache_references` | `int \| null` | Last-level cache accesses |
| `cache_misses` | `int \| null` | Last-level cache misses (absolute) |
| `cache_miss_pct` | `float \| null` | `cache_misses / cache_references × 100` |
| `elapsed_seconds` | `float \| null` | Wall-clock time of the profiled run |
| `cpu_utilized` | `float \| null` | Average CPUs used during the run |

Any field can be `null` if `perf` did not emit a line for that counter (e.g.
hardware PMU not available in a VM).

---

### `HotFunction`

One entry per function in the `perf report` output. Up to 20 returned, sorted
by `overhead_pct` descending.

```json
{
  "overhead_pct": 62.41,
  "samples": 1248,
  "symbol": "matrix_multiply",
  "dso": "test_prog"
}
```

| Field | Type | Notes |
|---|---|---|
| `overhead_pct` | `float` | % of samples in this function |
| `samples` | `int` | Raw sample count |
| `symbol` | `str` | Function name; `[unknown]` when no debug symbols |
| `dso` | `str` | Shared object / binary name |

Full profile is a `list[HotFunction]` (max 20 elements).

---

### LLM analysis text

Free-text string streamed from Ollama. Structured loosely as:

```
1) Key Observations — ...
2) Top Bottlenecks — ...
3) Recommendations — ...
```

No guaranteed sub-structure. For a backend, treat it as a markdown string.

---

## Mode 2 — Self-optimization (`--loops N`)

All of Mode 1 is produced first (initial profile + LLM analysis), then the
optimizer loop appends the following per iteration.

The loop can run **fully automated** (default) or **interactively** with
`--user-approved` (see [Interactive approval mode](#interactive-approval-mode--user-approved) below).

---

### `IterationRecord`

One record per optimization attempt, regardless of outcome.

```json
{
  "iteration": 2,
  "description": "Replace linked list traversal with flat array for cache locality",
  "kept": true,
  "elapsed_before": 1.344,
  "elapsed_after": 1.108,
  "delta_pct": -17.56,
  "ipc_before": 1.89,
  "ipc_after": 2.34,
  "compile_failed": false,
  "no_code_block": false,
  "revert_reason": "",
  "user_rejected": false,
  "user_feedback": ""
}
```

| Field | Type | Notes |
|---|---|---|
| `iteration` | `int` | 1-based counter |
| `description` | `str` | One-sentence summary from the LLM (`CHANGE:` line, or first non-empty line) |
| `kept` | `bool` | `true` = change was kept; either improvement ≥ 1% **or** user explicitly approved it |
| `elapsed_before` | `float \| null` | Wall-clock of the previous best run (seconds) |
| `elapsed_after` | `float \| null` | Wall-clock of the candidate run (seconds); `null` on compile failure or user rejection |
| `delta_pct` | `float` | `(after − before) / before × 100`; **negative = faster**; `0.0` on user rejection |
| `ipc_before` | `float \| null` | IPC before this attempt |
| `ipc_after` | `float \| null` | IPC of candidate; `null` on compile failure or user rejection |
| `compile_failed` | `bool` | Compiler returned non-zero; no profile was run |
| `no_code_block` | `bool` | LLM response had no ` ```c ``` ` block; loop stopped |
| `revert_reason` | `str` | Human-readable reason for rejection; empty string when `kept=true` |
| `user_rejected` | `bool` | `true` when the user explicitly rejected the proposal via `--user-approved`; no compile or profile was run |
| `user_feedback` | `str` | Feedback text the user typed on rejection; empty on `n`/`no`; injected into next LLM call |

`revert_reason` values you may see:

| Value | Meaning |
|---|---|
| `""` | Change was kept |
| `"Compilation failed"` | `compile_failed=true` |
| `"No \`\`\`c block in response"` | `no_code_block=true` |
| `"No improvement (+0.3% < 1%)"` | Within noise threshold |
| `"Profiling failed: <error>"` | perf subprocess error on candidate binary |
| `"User rejected"` | User typed `n` with no feedback (`--user-approved`) |
| `"User rejected: <text>"` | User typed a feedback message (`--user-approved`) |

---

### Full optimization run result

`run_optimize_loop` returns `(list[IterationRecord], Path)`.

```json
{
  "output_path": "optimized/test_prog.c",
  "history": [
    {
      "iteration": 1,
      "description": "Hoist invariant strlen() out of hot loop",
      "kept": false,
      "elapsed_before": 1.344,
      "elapsed_after": 1.331,
      "delta_pct": -0.97,
      "ipc_before": 1.89,
      "ipc_after": 1.91,
      "compile_failed": false,
      "no_code_block": false,
      "revert_reason": "No improvement (-0.97% < 1%)",
      "user_rejected": false,
      "user_feedback": ""
    },
    {
      "iteration": 2,
      "description": "Replace linked list traversal with flat array for cache locality",
      "kept": true,
      "elapsed_before": 1.344,
      "elapsed_after": 1.108,
      "delta_pct": -17.56,
      "ipc_before": 1.89,
      "ipc_after": 2.34,
      "compile_failed": false,
      "no_code_block": false,
      "revert_reason": "",
      "user_rejected": false,
      "user_feedback": ""
    },
    {
      "iteration": 3,
      "description": "Add __builtin_expect to branch in inner loop",
      "kept": false,
      "elapsed_before": 1.108,
      "elapsed_after": 1.342,
      "delta_pct": 21.12,
      "ipc_before": 2.34,
      "ipc_after": 1.92,
      "compile_failed": false,
      "no_code_block": false,
      "revert_reason": "No improvement (+21.12% < 1%)",
      "user_rejected": false,
      "user_feedback": ""
    }
  ]
}
```

`output_path` is the file written to disk (inside `optimized/` by default, or
whatever `--output-dir` was set to). It is present even when no iterations were
kept (it will simply not exist on disk in that case).

---

## Stop conditions

The loop ends early for any of these reasons. You can detect them from the
last record in `history`:

| Condition | Signal in history |
|---|---|
| Reached `--loops N` | `len(history) == N` |
| 3 consecutive rejections | Last 3 records all have `kept=false` and `compile_failed=false` |
| 3 consecutive user rejections | Last 3 records all have `user_rejected=true` |
| LLM gave up | Last record has `no_code_block=true`; or no record added and response contained `NO_FURTHER_OPTIMIZATIONS` |
| Compile failure streak | Last 3 records all have `compile_failed=true` |
| `KeyboardInterrupt` | History is whatever completed; partial results are valid |

User rejections count toward the same `consecutive_rejections` counter as
automatic rejections and compile failures.

---

## Interactive approval mode (`--user-approved`)

With `--user-approved` the loop pauses after each LLM proposal and before
any compilation. The terminal shows a unified diff of the proposed change in a
yellow **Proposed Change** panel, then prompts:

```
Accept this change? [y / n / feedback message]
```

| Input | Effect |
|---|---|
| `y`, `yes`, or empty Enter | Proposal is compiled, profiled, and **kept regardless of score**. `user_rejected=false`, `user_feedback=""`. |
| `n` or `no` | Proposal is skipped. `user_rejected=true`, `user_feedback=""`, `revert_reason="User rejected"`. |
| Any other text | Proposal is skipped. `user_rejected=true`, `user_feedback=<text>`, `revert_reason="User rejected: <text>"`. The feedback is injected into the LLM history for the next call as `[Feedback: <text>]`. |

Without `--user-approved` this gate does not exist and behaviour is identical
to previous versions. The two new `IterationRecord` fields (`user_rejected`,
`user_feedback`) are always present but default to `false`/`""`.

### Example — user-rejected record

```json
{
  "iteration": 1,
  "description": "Hoist strlen() out of inner loop",
  "kept": false,
  "elapsed_before": 1.344,
  "elapsed_after": null,
  "delta_pct": 0.0,
  "ipc_before": 1.89,
  "ipc_after": null,
  "compile_failed": false,
  "no_code_block": false,
  "revert_reason": "User rejected: use 64-byte cache blocks instead",
  "user_rejected": true,
  "user_feedback": "use 64-byte cache blocks instead"
}
```

The next LLM call will include this line in its optimization history table:

```
     1  USER REJECTED      N/A  Hoist strlen() out of inner loop  [Feedback: use 64-byte cache blocks instead]
```

---

## Score formula (internal — for reference)

The optimizer scores a run relative to baseline before deciding keep/reject:

```
score = 0.7 × (elapsed / baseline_elapsed)
      + 0.2 × (baseline_ipc / ipc)        # only if ipc available
      + 0.1 × (cache_miss_pct / baseline_cache_miss_pct)  # only if available

kept = (score_before − score_after) > 0.01
```

Lower score = better. `delta_pct` in `IterationRecord` uses raw elapsed only
(simpler to display), while the keep/reject decision uses the weighted score.

---

## Wiring into a backend

Replace the display callbacks in `OptimizeConfig` with functions that append to
a list, write to a queue, or emit SSE events. The dataclasses are already
serializable with `dataclasses.asdict()`.

```python
import dataclasses, json

results = []

config = OptimizeConfig(
    ...
    on_iteration_done=lambda rec: results.append(dataclasses.asdict(rec)),
    on_source_written=lambda old, new, path: None,  # handle diff however you like
    on_profile_done=lambda metrics, funcs: ...,     # StatMetrics + list[HotFunction]
    on_llm_response=lambda thinking, response, n: ...,
    on_compile_result=lambda r: ...,
    on_iteration_start=lambda n, max_n: ...,
    # Optional: intercept each proposal before compilation.
    # Return (True, "") to approve, (False, "") to reject silently,
    # or (False, "feedback text") to reject with feedback for the LLM.
    on_user_approval=None,   # None = fully automatic (default)
)

history, output_path = run_optimize_loop(config)
print(json.dumps(dataclasses.asdict(history[0]), indent=2))
```

All callbacks are optional to wire up — pass no-op lambdas for anything you
don't need. The only side-effect that matters for correctness is
`on_source_written`, and you can ignore that too if you handle the output file
yourself.

`on_user_approval` signature: `(current_source: str, proposed_source: str) -> (approved: bool, feedback: str)`.
When `approved=True` the change is compiled, profiled, and **kept regardless of
score**. When `approved=False` no compilation happens; non-empty `feedback` is
injected into the next LLM call's history.
