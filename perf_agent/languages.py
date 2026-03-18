"""Language specifications for multi-language perf-agent support."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LanguageSpec:
    name: str                        # "c", "cpp", "rust", "python", "java", "javascript"
    display_name: str                # "C", "C++", "Rust", "Python", "Java", "JavaScript"
    extensions: tuple[str, ...]      # (".c",), (".cpp", ".cc", ".cxx"), ...
    fence: str                       # LLM code-fence tag: "c", "cpp", "rust", ...
    compiled: bool                   # True → has a build step
    needs_elf_check: bool            # True only for compiled langs producing ELF binaries
    default_compiler: str            # "gcc", "g++", "rustc", "javac", ""
    default_flags: str               # "-O2 -g …" or ""
    runtime: str                     # interpreter/runtime cmd or "" ("python3", "node", "java")
    security_layers: tuple[str, ...] # subset of {"static_c","cppcheck","sanitizer","bandit","llm"}
    llm_context: str                 # injected into optimize system prompt


LANGUAGES: dict[str, LanguageSpec] = {
    "c": LanguageSpec(
        name="c", display_name="C",
        extensions=(".c",),
        fence="c", compiled=True, needs_elf_check=True,
        default_compiler="gcc",
        default_flags="-O2 -g -fno-omit-frame-pointer",
        runtime="",
        security_layers=("static_c", "cppcheck", "sanitizer", "llm"),
        llm_context="",
    ),
    "cpp": LanguageSpec(
        name="cpp", display_name="C++",
        extensions=(".cpp", ".cc", ".cxx", ".C"),
        fence="cpp", compiled=True, needs_elf_check=True,
        default_compiler="g++",
        default_flags="-O2 -std=c++17 -g -fno-omit-frame-pointer",
        runtime="",
        security_layers=("static_c", "cppcheck", "sanitizer", "llm"),
        llm_context=(
            "Target is C++17. Prefer std::string_view over copies, move semantics, "
            "reserve containers, avoid virtual dispatch in hot paths, "
            "use __builtin_expect, prefer stack allocation."
        ),
    ),
    "rust": LanguageSpec(
        name="rust", display_name="Rust",
        extensions=(".rs",),
        fence="rust", compiled=True, needs_elf_check=True,
        default_compiler="rustc",
        default_flags="-C opt-level=2 -C debuginfo=2 -C force-frame-pointers=yes",
        runtime="",
        security_layers=("llm",),
        llm_context=(
            "Target is Rust. Prefer iterator chains (auto-vectorised by LLVM), "
            "avoid heap allocation in hot loops, use Rayon for data parallelism, "
            "prefer slices over Vec in hot paths, avoid Arc/Mutex in the critical path, "
            "use #[inline] on hot small functions."
        ),
    ),
    "python": LanguageSpec(
        name="python", display_name="Python",
        extensions=(".py",),
        fence="python", compiled=False, needs_elf_check=False,
        default_compiler="", default_flags="",
        runtime="python3",
        security_layers=("bandit", "llm"),
        llm_context=(
            "Target is CPython. Replace Python loops with NumPy/Pandas vectorised ops, "
            "use built-in functions (map/filter/sum), cache global lookups in locals, "
            "use __slots__ on hot objects, prefer list comprehensions, "
            "avoid repeated attribute lookups in hot loops."
        ),
    ),
    "java": LanguageSpec(
        name="java", display_name="Java",
        extensions=(".java",),
        fence="java", compiled=True, needs_elf_check=False,
        default_compiler="javac", default_flags="",
        runtime="java",
        security_layers=("llm",),
        llm_context=(
            "Target is JVM. Avoid object allocation in hot paths (reuse/pool objects, "
            "use primitives), prefer ArrayList over LinkedList, use StringBuilder not "
            "string concatenation, mark hot classes/methods final for JIT devirtualisation, "
            "avoid autoboxing, use System.arraycopy for bulk array copies."
        ),
    ),
    "javascript": LanguageSpec(
        name="javascript", display_name="JavaScript",
        extensions=(".js", ".mjs", ".cjs"),
        fence="javascript", compiled=False, needs_elf_check=False,
        default_compiler="", default_flags="",
        runtime="node",
        security_layers=("llm",),
        llm_context=(
            "Target is Node.js (V8). Keep call sites monomorphic, use TypedArrays for "
            "numeric data, avoid try/catch in hot loops, prefer for-loops over forEach "
            "in perf-critical paths, avoid dynamic property addition after object creation."
        ),
    ),
    "go": LanguageSpec(
        name="go", display_name="Go",
        extensions=(".go",),
        fence="go", compiled=True, needs_elf_check=True,
        default_compiler="go",
        default_flags="",          # standard Go optimizer; frame pointers on by default (Go ≥1.21 / amd64)
        runtime="",
        security_layers=("llm",),
        llm_context=(
            "Target is Go. Avoid interface{} boxing in hot paths, reuse objects with "
            "sync.Pool, use slices instead of maps where ordering allows, prefer value "
            "receivers for small structs, avoid per-iteration goroutine spawning, "
            "use copy/append over manual loops for slice ops, prefer []byte over string "
            "in serialisation paths."
        ),
    ),
}


def detect_language(source: Path) -> LanguageSpec | None:
    """Infer LanguageSpec from file extension. Returns None if unknown."""
    ext = source.suffix.lower()
    for spec in LANGUAGES.values():
        if ext in spec.extensions:
            return spec
    return None


def get_language(name: str) -> LanguageSpec:
    if name not in LANGUAGES:
        raise ValueError(
            f"Unknown language {name!r}. Supported: {', '.join(sorted(LANGUAGES))}"
        )
    return LANGUAGES[name]
