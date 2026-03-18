"""Target architecture specifications for Docker-based profiling."""

from __future__ import annotations

from dataclasses import dataclass

_HW_EVENTS = (
    "task-clock,cpu-cycles,instructions,branches,branch-misses,"
    "cache-references,cache-misses"
)
_SW_EVENTS = "task-clock,context-switches,cpu-migrations,page-faults,instructions,branches"


@dataclass(frozen=True)
class TargetSpec:
    name: str
    description: str
    dockerfile: str           # filename inside dockerfiles/
    platform: str             # "linux/amd64" or "linux/arm64/v8"
    compiler: str             # "gcc-12", "clang-17", etc.
    compile_flags: str
    perf_events: str
    software_events_only: bool
    llm_context: str          # injected into LLM system prompt


CATALOG: dict[str, TargetSpec] = {
    "generic": TargetSpec(
        "generic",
        "Ubuntu 22.04, GCC 12, -O2",
        "Dockerfile.generic",
        "linux/amd64",
        "gcc-12",
        "-O2 -g -fno-omit-frame-pointer",
        _HW_EVENTS,
        False,
        "Generic x86-64, GCC 12, -O2.",
    ),
    "amd-zen3": TargetSpec(
        "amd-zen3",
        "Ubuntu 22.04, GCC 13, -march=znver3",
        "Dockerfile.amd-zen3",
        "linux/amd64",
        "gcc-13",
        "-O3 -march=znver3 -g -fno-omit-frame-pointer",
        _HW_EVENTS,
        False,
        "AMD Zen 3 (-march=znver3), GCC 13, -O3. Favour AVX2, large L3, 6-wide superscalar.",
    ),
    "intel-skylake": TargetSpec(
        "intel-skylake",
        "Ubuntu 22.04, GCC 12, -march=skylake",
        "Dockerfile.intel-skylake",
        "linux/amd64",
        "gcc-12",
        "-O3 -march=skylake -g -fno-omit-frame-pointer",
        _HW_EVENTS,
        False,
        "Intel Skylake (-march=skylake), GCC 12, -O3. AVX2, 224-entry ROB, hyperthreading.",
    ),
    "clang-lto": TargetSpec(
        "clang-lto",
        "Ubuntu 22.04, Clang 17, -O2 -flto",
        "Dockerfile.clang-lto",
        "linux/amd64",
        "clang-17",
        "-O2 -flto -g -fno-omit-frame-pointer",
        _HW_EVENTS,
        False,
        "x86-64, Clang 17, -O2 -flto. LTO active; cross-function inlining available.",
    ),
    "arm64": TargetSpec(
        "arm64",
        "Ubuntu 22.04 arm64 (QEMU), software events",
        "Dockerfile.arm64",
        "linux/arm64/v8",
        "gcc-12",
        "-O2 -g -fno-omit-frame-pointer",
        _SW_EVENTS,
        True,
        "ARM64/AArch64, GCC 12, -O2. QEMU — hardware counters unavailable. Avoid x86-specific builtins; prefer NEON.",
    ),
    # --- Edge: Raspberry Pi ---
    "rpi3": TargetSpec(
        "rpi3", "Raspberry Pi 3 — ARMv7 Cortex-A53 (QEMU arm32)",
        "Dockerfile.arm32", "linux/arm/v7", "gcc",
        "-O2 -march=armv8-a+crc -mcpu=cortex-a53 -mfloat-abi=hard -mfpu=neon-fp-armv8 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Raspberry Pi 3 Cortex-A53: in-order dual-issue, 512 KB L2, LPDDR2. "
        "No out-of-order execution — avoid long dependency chains. Prefer NEON SIMD, minimize cache misses.",
    ),
    "rpi4": TargetSpec(
        "rpi4", "Raspberry Pi 4 — ARM64 Cortex-A72 (QEMU arm64)",
        "Dockerfile.arm64", "linux/arm64/v8", "gcc-12",
        "-O2 -march=armv8-a+crc+simd -mcpu=cortex-a72 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Raspberry Pi 4 Cortex-A72: out-of-order 3-wide, 1 MB L2, LPDDR4. "
        "Favour NEON vectorisation; branch predictor stronger than A53.",
    ),
    "rpi5": TargetSpec(
        "rpi5", "Raspberry Pi 5 — ARM64 Cortex-A76 (QEMU arm64)",
        "Dockerfile.arm64-gcc13", "linux/arm64/v8", "gcc-13",
        "-O3 -march=armv8.2-a+crypto+dotprod -mcpu=cortex-a76 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Raspberry Pi 5 Cortex-A76: wide out-of-order 4-wide, 512 KB L2 per core, LPDDR4X. "
        "Supports dotprod and crypto extensions; strong FP/SIMD throughput.",
    ),
    # --- Edge: NVIDIA Jetson ---
    "jetson-nano": TargetSpec(
        "jetson-nano", "NVIDIA Jetson Nano — ARM64 Cortex-A57 (QEMU arm64)",
        "Dockerfile.arm64", "linux/arm64/v8", "gcc-12",
        "-O2 -march=armv8-a -mcpu=cortex-a57 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Jetson Nano Cortex-A57: out-of-order 3-wide, 2 MB L2 shared, unified memory with GPU. "
        "Memory bus shared with GPU — minimise unnecessary allocations.",
    ),
    "jetson-orin": TargetSpec(
        "jetson-orin", "NVIDIA Jetson Orin — ARM64 Cortex-A78AE (QEMU arm64)",
        "Dockerfile.arm64-gcc13", "linux/arm64/v8", "gcc-13",
        "-O3 -march=armv8.2-a+crypto+dotprod -mcpu=cortex-a78ae -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Jetson Orin Cortex-A78AE: wide out-of-order, LPDDR5, ML accelerator present. "
        "Automotive-grade; prefer cache-friendly access and dot-product vectorisation.",
    ),
    # --- Cloud VPS tiers ---
    "vps-small": TargetSpec(
        "vps-small", "Small cloud VPS — 1 vCPU, generic x86-64",
        "Dockerfile.generic", "linux/amd64", "gcc-12",
        "-O2 -march=x86-64 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Small VPS: 1 shared vCPU, ~1-2 GB RAM, KVM hypervisor, no hardware perf counters. "
        "Optimise for algorithmic complexity and memory footprint over SIMD.",
    ),
    "vps-medium": TargetSpec(
        "vps-medium", "Medium cloud VPS — 2-4 vCPU, SSE4.2 baseline",
        "Dockerfile.generic", "linux/amd64", "gcc-12",
        "-O2 -march=x86-64-v2 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Medium VPS: 2-4 vCPU, ~4-8 GB RAM, x86-64-v2 (SSE4.2/POPCNT). "
        "Thread-safe code preferred; avoid over-specialised SIMD beyond SSE4.2.",
    ),
    "vps-large": TargetSpec(
        "vps-large", "Large cloud VPS — 4-8 vCPU, AVX2",
        "Dockerfile.generic", "linux/amd64", "gcc-12",
        "-O3 -march=x86-64-v3 -g -fno-omit-frame-pointer",
        _SW_EVENTS, True,
        "Large VPS: 4-8 vCPU, ~16 GB RAM, x86-64-v3 (AVX2/FMA). "
        "AVX2 vectorisation viable; parallelism beneficial but NUMA effects possible.",
    ),
}


def get_target(name: str) -> TargetSpec:
    if name not in CATALOG:
        raise ValueError(
            f"Unknown target {name!r}. Available: {', '.join(sorted(CATALOG))}"
        )
    return CATALOG[name]
