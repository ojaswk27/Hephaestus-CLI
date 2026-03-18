# How to Run perf-agent

## Prerequisites

- Linux (requires the Linux `perf` subsystem)
- Python 3.11+
- `perf` installed (`sudo pacman -S perf` / `sudo apt install linux-perf`)
- An LLM endpoint — either:
  - [Ollama](https://ollama.com) running locally (`ollama pull qwen2.5:7b`)
  - OpenAI API key in `OPENAI_API_KEY`
- Language toolchains for the languages you want to optimize:
  - C/C++: `gcc` / `g++`
  - Rust: `rustc`
  - Go: `go`
  - Java: `jdk` (`javac` + `java`)
  - JavaScript: `node`
  - Python: `python3`
- Docker (only for `--target` cross-architecture runs)

## Install

```bash
pip install -e /home/monarq/Work/hackdata-26
```

## Quick start — profile and analyse

```bash
# Analysis only (no optimization) — profile a pre-built binary
perf-agent /tmp/my_binary

# Profile an interpreted script directly
perf-agent my_script.py
perf-agent my_script.js

# Compile + profile a C source file (agent compiles it for you)
perf-agent --source test_prog.c test_prog.c
```

## Optimization loop — any language

Pass `--loops N` and `--source` to enter the iterative optimization loop.
The agent profiles, asks the LLM for one improvement, compiles, re-profiles,
and keeps or reverts based on measured elapsed time.

```bash
# C
perf-agent --loops 5 --source test_prog.c test_prog.c

# C++
perf-agent --loops 5 --source test_prog.cpp test_prog.cpp

# Rust
perf-agent --loops 5 --source test_prog.rs test_prog.rs

# Go
perf-agent --loops 5 --source test_prog.go test_prog.go

# Java  (class name must match filename stem)
perf-agent --loops 5 --source test_prog.java test_prog.java

# Python  (interpreted — no separate binary needed)
perf-agent --loops 5 --source test_prog.py test_prog.py

# JavaScript
perf-agent --loops 5 --source test_prog.js test_prog.js
```

Language is auto-detected from the file extension. Override with `--lang`:

```bash
perf-agent --lang cpp --loops 3 --source code.cc code.cc
```

Optimized source is written to `optimized/` next to the source file (override with `--output-dir`).

### Loop termination

The loop stops early when any of the following is true:

- 3 consecutive iterations produce no improvement
- The LLM emits `NO_FURTHER_OPTIMIZATIONS`
- The current elapsed time is within 5% of the theoretical hardware ceiling
- `--loops` limit reached
- Ctrl-C

## Using Ollama (local LLM)

```bash
# Pull a model
ollama pull qwen2.5:7b

# Run with Ollama
perf-agent --loops 5 --source test_prog.c \
           --model qwen2.5:7b \
           --base-url http://localhost:11434/v1 \
           --api-key ollama \
           test_prog.c
```

Or set defaults in a `.env` file at the project root:

```
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
```

Then just:

```bash
perf-agent --loops 5 --source test_prog.c --model qwen2.5:7b test_prog.c
```

## Security features

### Multilayer security gate

Every candidate the LLM produces is checked before it is accepted:

| Layer | Languages | What it checks |
|---|---|---|
| Regex scan | C, C++ | Dangerous calls: `gets`, `strcpy`, `sprintf`, `system`, etc. |
| `cppcheck` | C, C++ | Undefined behaviour, buffer overruns, memory leaks |
| ASan/UBSan/LSan | C, C++ | Runtime sanitizers (compiles + runs candidate) |
| `bandit` | Python | Security anti-patterns |
| LLM audit | All | Language-specific vulnerability review |

Candidates that fail any layer are rejected and the LLM is prompted again.

Skip the gate entirely with `--no-security` (useful for benchmarking or trusted code):

```bash
perf-agent --loops 5 --source test_prog.c --no-security test_prog.c
```

### Security remediation pre-pass

Before the optimization loop begins, the agent scans the *original* source for
security issues and asks the LLM to fix them first. Skip with `--no-remediate`:

```bash
perf-agent --loops 5 --source test_prog.c --no-remediate test_prog.c
```

## User-approval mode

Review and optionally reject each LLM proposal before it is compiled:

```bash
perf-agent --loops 10 --source test_prog.c --user-approved test_prog.c
```

At each proposal you will see a unified diff and a prompt:

- `y` / Enter — accept
- `n` — reject silently
- Any other text — reject and feed the text back to the LLM as guidance

## All options

| Flag | Default | Description |
|---|---|---|
| `--loops N` | `0` | Optimization iterations (0 = analysis only) |
| `--source PATH` | — | Source file to optimize (required for `--loops > 0`) |
| `--lang LANG` | auto | Language override: `c` `cpp` `rust` `go` `java` `javascript` `python` |
| `--compiler CMD` | language default | Compiler command override |
| `--compile-flags FLAGS` | language default | Compiler flags override |
| `--output-dir DIR` | `optimized/` | Where to write the best source |
| `--model NAME` | `gpt-4o` | LLM model name |
| `--base-url URL` | — | OpenAI-compatible API base URL (e.g. Ollama) |
| `--api-key KEY` | `$OPENAI_API_KEY` | API key override |
| `--timeout N` | `120` | perf execution timeout (seconds) |
| `--no-think` | off | Disable chain-of-thought (faster) |
| `--no-security` | off | Skip the multilayer security gate |
| `--no-remediate` | off | Skip the LLM security remediation pre-pass |
| `--user-approved` | off | Pause and ask before each compile |
| `--target NAME` | — | Build and profile inside Docker |
| `--list-targets` | — | Print available Docker targets and exit |
| `--no-build` | off | Skip Docker image build |

## Docker targets (cross-architecture)

Build and profile inside a container to test architecture-specific compiler
flags without native hardware. Requires Docker and QEMU binfmt for ARM targets.

```bash
# List available targets
perf-agent --list-targets

# Optimize a C file for AMD Zen 3
perf-agent --target amd-zen3 --loops 5 --source test_prog.c test_prog.c

# Optimize for Raspberry Pi 5 (QEMU arm64)
perf-agent --target rpi5 --loops 3 --source test_prog.c test_prog.c
```

### QEMU setup (ARM targets)

```bash
# Arch Linux
sudo pacman -S qemu-user-static
sudo systemctl restart docker
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# Debian/Ubuntu
sudo apt install qemu-user-static
```

### Available targets

| Name | Platform | Compiler | Notes |
|---|---|---|---|
| `generic` | x86-64 | GCC 12 | Ubuntu 22.04, `-O2` |
| `amd-zen3` | x86-64 | GCC 13 | `-O3 -march=znver3` |
| `intel-skylake` | x86-64 | GCC 12 | `-O3 -march=skylake` |
| `clang-lto` | x86-64 | Clang 17 | `-O2 -flto` |
| `rpi3` | arm/v7 | GCC | Raspberry Pi 3 Cortex-A53 (QEMU, SW counters) |
| `rpi4` | arm64 | GCC 12 | Raspberry Pi 4 Cortex-A72 (QEMU, SW counters) |
| `rpi5` | arm64 | GCC 13 | Raspberry Pi 5 Cortex-A76 (QEMU, SW counters) |
| `jetson-nano` | arm64 | GCC 12 | Jetson Nano Cortex-A57 (QEMU, SW counters) |
| `jetson-orin` | arm64 | GCC 13 | Jetson Orin Cortex-A78AE (QEMU, SW counters) |
| `vps-small` | x86-64 | GCC 12 | 1 vCPU, `-march=x86-64` (SW counters) |
| `vps-medium` | x86-64 | GCC 12 | 2-4 vCPU, `-march=x86-64-v2` (SW counters) |
| `vps-large` | x86-64 | GCC 12 | 4-8 vCPU, `-march=x86-64-v3` AVX2 (SW counters) |

> ARM and VPS targets use software-only perf counters (no hardware PMU in QEMU/VM).

## perf permissions

If perf reports a permissions error:

```bash
sudo sysctl kernel.perf_event_paranoid=1
```

Make it permanent in `/etc/sysctl.d/99-perf.conf`:

```
kernel.perf_event_paranoid = 1
```
