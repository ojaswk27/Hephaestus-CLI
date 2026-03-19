Here's the Claude Code prompt:

---

**SYSTEM / PROJECT PROMPT — Assembly Optimization Engine**

You are building an automated assembly profiling, benchmarking, and optimization system. Build this iteratively, file by file, testing as you go.

---

**OVERVIEW**

Build a Python-based orchestration system that:
1. Takes an assembly source file as input
2. Runs it inside Docker containers across multiple architectures
3. Collects a rich set of metrics
4. Sends the code + metrics to the Claude API for optimization suggestions
5. Applies the suggestion, re-benchmarks, and repeats
6. Tracks all runs in a database and generates visual reports

---

**ARCHITECTURE**

```
asm-optimizer/
├── orchestrator.py          # main loop
├── docker_runner.py         # container spin-up, exec, teardown
├── profiler.py              # metric collection per arch
├── llm_optimizer.py         # Claude API calls for suggestions
├── benchmark.py             # timing harness, statistical analysis
├── metrics.py               # metric schema + storage (SQLite)
├── report.py                # generate charts and summary
├── containers/
│   ├── Dockerfile.amd64
│   ├── Dockerfile.arm64
│   └── Dockerfile.riscv64
└── results/                 # output DB, charts, logs
```

---

**STEP 1 — Docker Containers**

Create three Dockerfiles. Each must include:
- The appropriate GNU assembler (`gas`) or `nasm`
- `perf` (where available), `valgrind`, `time`
- A small HTTP server (Flask or just socat) that accepts: `{ "asm": "...", "action": "assemble|benchmark|profile" }` and returns JSON results
- For ARM64: install `gcc-aarch64-linux-gnu` cross-toolchain on the host if native ARM unavailable; prefer `--platform linux/arm64` with QEMU via `tonistiigi/binfmt`

Register QEMU binfmt handlers first:
```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

---

**STEP 2 — Metrics Collection**

Collect ALL of the following metrics per benchmark run. Store them in SQLite with schema `(run_id, arch, iteration, metric_name, value, timestamp)`:

**Execution Performance**
- `wall_time_p50`, `wall_time_p95`, `wall_time_p99` — run 100 iterations, take percentiles
- `cpu_cycles` — via RDTSC (x86) or `cntvct_el0` (ARM)
- `instructions_retired` — via `perf stat`
- `ipc` — instructions per cycle
- `cpi` — cycles per instruction

**Memory**
- `peak_rss_kb` — via `/usr/bin/time -v`
- `heap_alloc_count`, `heap_alloc_bytes` — via `valgrind --tool=massif`
- `l1_cache_miss_rate`, `l2_cache_miss_rate`, `l3_cache_miss_rate` — via `perf stat -e cache-misses,cache-references`
- `tlb_miss_count` — via `perf stat -e dTLB-load-misses`
- `page_faults_minor`, `page_faults_major`

**Parallelism** (if the code uses threads)
- `speedup_curve` — benchmark at 1, 2, 4, 8 threads; store as JSON array
- `amdahl_efficiency` — `speedup / num_threads`
- `lock_contention_ns` — via `perf lock`
- `context_switch_rate` — via `perf stat -e context-switches`
- `thread_idle_pct`

**Pipeline / Microarchitecture** (x86 only via perf)
- `branch_mispredict_rate` — `perf stat -e branch-misses,branches`
- `frontend_stall_cycles`, `backend_stall_cycles`
- `retiring_slots_pct`

**Vectorization**
- `simd_instructions_pct` — ratio of SIMD to total instructions via `perf stat`
- `vector_lane_utilization` — estimated from instruction mix

**Energy** (x86 only)
- `rapl_joules` — read `/sys/class/powercap/intel-rapl/*/energy_uj` before and after
- `perf_per_watt` — `instructions_retired / rapl_joules`

**Code Quality**
- `binary_size_bytes` — size of assembled `.o` or ELF
- `instruction_count` — total instructions in hot section
- `register_spill_count` — parse from compiler/assembler output if available

**Composite**
- `roofline_compute_intensity` — FLOP/byte ratio (estimate from instruction mix)
- `optimization_roi` — `(new_ipc - old_ipc) / lines_changed`
- `universality_score` — fraction of architectures where this change improved performance

---

**STEP 3 — LLM Optimization Loop**

In `llm_optimizer.py`, call the Claude API (`claude-sonnet-4-20250514`) with a structured prompt:

```
You are an expert assembly optimizer. 

Target architecture: {arch}
Current assembly code:
<code>
{asm_code}
</code>

Profiling results from last run:
{metrics_json}

Bottleneck analysis:
- IPC: {ipc} (target >3.0)
- L1 miss rate: {l1_miss_rate}%
- Branch misprediction: {branch_mispredict_rate}%
- Backend stall cycles: {backend_stall_pct}%

Previous optimizations tried (do not repeat):
{previous_attempts}

Suggest exactly ONE optimization. Respond in JSON:
{
  "optimization_class": "vectorization|loop_unrolling|instruction_scheduling|...",
  "rationale": "...",
  "modified_asm": "...",
  "expected_improvement": "..."
}
```

Parse the JSON response, extract `modified_asm`, pass to the container for assembly + benchmark.

---

**STEP 4 — Main Orchestration Loop**

```python
MAX_ITERATIONS = 20
MIN_IMPROVEMENT_PCT = 1.0
STALL_AFTER = 5  # stop if no improvement for N consecutive iterations

for arch in ["linux/amd64", "linux/arm64", "linux/riscv64"]:
    container = docker_runner.spin_up(arch)
    baseline = profiler.full_profile(container, original_asm)
    db.store(run_id, arch, iteration=0, metrics=baseline)
    
    no_improve_streak = 0
    for i in range(1, MAX_ITERATIONS + 1):
        candidate_asm = llm_optimizer.suggest(
            asm=current_asm,
            metrics=baseline,
            arch=arch,
            previous_attempts=db.get_attempts(run_id, arch)
        )
        
        # validate correctness first
        if not validator.outputs_match(container, original_asm, candidate_asm):
            db.store_rejected(run_id, arch, i, reason="correctness")
            continue
        
        result = profiler.full_profile(container, candidate_asm)
        db.store(run_id, arch, iteration=i, metrics=result)
        
        improvement = (baseline.wall_time_p50 - result.wall_time_p50) / baseline.wall_time_p50 * 100
        
        if improvement >= MIN_IMPROVEMENT_PCT:
            current_asm = candidate_asm
            baseline = result
            no_improve_streak = 0
        else:
            no_improve_streak += 1
            
        if no_improve_streak >= STALL_AFTER:
            break
    
    docker_runner.teardown(container)
```

---

**STEP 5 — Correctness Validation**

Before accepting any optimized version:
- Run both original and candidate with identical inputs
- Compare stdout + exit code
- For numeric output, allow epsilon tolerance (configurable)
- If mismatch: reject, log, tell LLM "this produced incorrect output, try again"

---

**STEP 6 — Reporting**

In `report.py`, generate using `matplotlib` and `pandas`:

1. **Speedup timeline** — line chart of `wall_time_p50` per iteration per arch
2. **Metric heatmap** — all metrics × all iterations, normalized 0-1
3. **Thread scaling curve** — `speedup_curve` plotted against ideal Amdahl line
4. **Roofline plot** — compute intensity vs bandwidth, mark each iteration
5. **Universality matrix** — optimization_class × arch, colored by improvement %
6. **Final summary table** — best version per arch, total improvement %, metrics delta

Save all charts to `results/report_{run_id}/` and generate a `summary.md`.

---

**IMPLEMENTATION ORDER**

Build and test in this order:
1. `containers/Dockerfile.amd64` + basic benchmark endpoint
2. `docker_runner.py` — spin up, exec, teardown
3. `metrics.py` — SQLite schema, store/query
4. `profiler.py` — collect all metrics from a running container
5. `benchmark.py` — statistical harness (100 runs, percentiles)
6. `llm_optimizer.py` — Claude API integration
7. `orchestrator.py` — main loop
8. `report.py` — charts and summary
9. ARM64 + RISCV64 Dockerfiles
10. End-to-end test with a sample bubblesort in x86-64 assembly

---

**CONSTRAINTS**
- All benchmark runs: pin to CPU core 0 (`taskset 0x1`), disable address space randomization (`setarch -R`), run 100 samples minimum
- Emulated architectures (ARM64/RISCV64 under QEMU): label metrics as `emulated=true` in DB, exclude from timing comparisons, use only for correctness + instruction mix analysis
- Never mutate the original input file — all candidates are stored in DB only
- The system must work on a Linux host with Docker installed; document any `sudo` requirements

---

Start by creating the project structure and `containers/Dockerfile.amd64`.
