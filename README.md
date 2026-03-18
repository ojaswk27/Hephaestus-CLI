# Hephaestus

An AI-powered Linux `perf` profiler that profiles your code, explains bottlenecks, and iteratively rewrites it to be faster — with a multilayer security gate on every candidate it produces.

## How it works

1. **Profile** — runs `perf stat` + `perf record` on your program
2. **Analyse** — streams an LLM explanation of the bottlenecks
3. **Optimize loop** — proposes one change per iteration, compiles, re-profiles, keeps or reverts based on measured elapsed time
4. **Security gate** — every LLM-generated candidate is scanned before it is accepted (regex → cppcheck → ASan/UBSan → LLM audit)
5. **Correctness gate** — candidate output is compared against the original; pytest / `go test` used automatically when present

## Install

```bash
pip install -e .
```

Requires Python 3.11+, Linux, and `perf`:

```bash
# Arch
sudo pacman -S perf

# Debian / Ubuntu
sudo apt install linux-perf
```

Set `kernel.perf_event_paranoid` if perf complains about permissions:

```bash
sudo sysctl kernel.perf_event_paranoid=1
```

## Quick start

```bash
# Profile + analyse (no optimization)
perf-agent my_program

# Optimize a C file for 5 iterations
perf-agent --loops 5 --source prog.c prog.c
```

## Languages

| Language | Extension | Compiler default |
|---|---|---|
| C | `.c` | `gcc -O2` |
| C++ | `.cpp` `.cc` `.cxx` | `g++ -O2 -std=c++17` |
| Rust | `.rs` | `rustc -C opt-level=2` |
| Go | `.go` | `go build` |
| Java | `.java` | `javac` + `java` |
| Python | `.py` | `python3` (interpreted) |
| JavaScript | `.js` | `node` (interpreted) |

Language is auto-detected from the file extension. Override with `--lang`.

## LLM providers

```bash
# OpenAI (default)
export OPENAI_API_KEY=sk-...
perf-agent --loops 5 --source prog.c prog.c

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...
perf-agent --loops 5 --source prog.c --model claude-sonnet-4-6 prog.c

# Ollama (local)
perf-agent --loops 5 --source prog.c \
           --model qwen2.5:7b \
           --base-url http://localhost:11434/v1 \
           --api-key ollama \
           prog.c
```

Provider is auto-detected from the model name (`claude-*` → Anthropic, everything else → OpenAI-compatible).

## Docker targets

Build and profile inside a container to test architecture-specific flags without native hardware.

```bash
# List all targets
perf-agent --list-targets

# Optimize for Raspberry Pi 5
perf-agent --target rpi5 --loops 3 --source prog.c prog.c

# Optimize for AMD Zen 3
perf-agent --target amd-zen3 --loops 5 --source prog.c prog.c
```

| Target | Platform | Notes |
|---|---|---|
| `generic` | x86-64 | GCC 12, `-O2` |
| `amd-zen3` | x86-64 | GCC 13, `-O3 -march=znver3` |
| `intel-skylake` | x86-64 | GCC 12, `-O3 -march=skylake` |
| `clang-lto` | x86-64 | Clang 17, `-O2 -flto` |
| `rpi3` | arm/v7 | Raspberry Pi 3 Cortex-A53 (QEMU) |
| `rpi4` | arm64 | Raspberry Pi 4 Cortex-A72 (QEMU) |
| `rpi5` | arm64 | Raspberry Pi 5 Cortex-A76 (QEMU) |
| `jetson-nano` | arm64 | Jetson Nano Cortex-A57 (QEMU) |
| `jetson-orin` | arm64 | Jetson Orin Cortex-A78AE (QEMU) |
| `vps-small` | x86-64 | 1 vCPU, `-march=x86-64` |
| `vps-medium` | x86-64 | 2–4 vCPU, SSE4.2 |
| `vps-large` | x86-64 | 4–8 vCPU, AVX2 |

ARM targets require QEMU binfmt:

```bash
sudo pacman -S qemu-user-static   # Arch
sudo apt install qemu-user-static  # Debian/Ubuntu
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

## Security features

Every LLM-generated candidate passes through a multilayer gate before being compiled or profiled:

| Layer | Languages | What it checks |
|---|---|---|
| Regex scan | C, C++ | Dangerous calls (`gets`, `strcpy`, `system`, …) |
| `cppcheck` | C, C++ | Undefined behaviour, buffer overruns, memory leaks |
| ASan / UBSan / LSan | C, C++ | Runtime sanitizers |
| `bandit` | Python | Security anti-patterns |
| LLM audit | All | Language-specific vulnerability review |

The original source is also scanned before the loop begins; the LLM is asked to fix any issues first (skip with `--no-remediate`).

## All options

| Flag | Default | Description |
|---|---|---|
| `--loops N` | `0` | Optimization iterations (0 = analysis only) |
| `--source PATH` | — | Source file to optimize |
| `--lang LANG` | auto | `c` `cpp` `rust` `go` `java` `javascript` `python` |
| `--compiler CMD` | language default | Compiler override |
| `--compile-flags FLAGS` | language default | Compiler flags override |
| `--output-dir DIR` | `optimized/` | Where to write the best source |
| `--model NAME` | `gpt-4o` | LLM model name |
| `--base-url URL` | — | OpenAI-compatible API base URL |
| `--api-key KEY` | env var | API key override |
| `--timeout N` | `120` | perf execution timeout (seconds) |
| `--check-cmd CMD` | auto | Shell command to verify output correctness |
| `--no-think` | off | Disable chain-of-thought |
| `--no-security` | off | Skip the security gate |
| `--no-remediate` | off | Skip the security remediation pre-pass |
| `--user-approved` | off | Review each proposal before compiling |
| `--target NAME` | — | Build and profile inside Docker |
| `--list-targets` | — | Print available Docker targets and exit |
| `--no-build` | off | Skip Docker image build |

### Loop termination

The loop stops early when any of the following is true:

- 3 consecutive iterations produce no improvement
- The LLM emits `NO_FURTHER_OPTIMIZATIONS`
- Current performance is within 5% of the theoretical hardware ceiling
- `--loops` limit reached
- Ctrl-C
