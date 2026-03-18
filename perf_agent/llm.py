"""LLM client — supports OpenAI-compatible APIs and Anthropic (Claude) natively.

Provider is auto-detected from the model name:
  - Models starting with "claude" → Anthropic SDK (native messages API)
  - All others                     → OpenAI SDK (chat completions / Ollama-compatible)

Prompt templates live in perf_agent/prompts/{openai,claude}.json so they can be
tuned without touching Python code.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import openai as _openai
from openai import OpenAI

from .errors import LLMConnectionError, LLMModelNotFoundError, NoCodeBlockError
from .parser import HotFunction, StatMetrics

if TYPE_CHECKING:
    from .languages import LanguageSpec
    from .optimizer import IterationRecord

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_MAX_REPORT_CHARS = 8000
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_CHANGE_LINE_RE = re.compile(r"^CHANGE:\s*(.+)$", re.MULTILINE)


def detect_provider(model: str) -> str:
    """Return 'anthropic' for claude-* models, 'openai' for everything else."""
    return "anthropic" if model.lower().startswith("claude") else "openai"


# ---------------------------------------------------------------------------
# Prompt template loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _load_prompts(provider: str) -> dict:
    path = _PROMPTS_DIR / f"{provider}.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _make_optimize_system(lang: "LanguageSpec", provider: str, target_context: str | None = None) -> str:
    prompts = _load_prompts(provider)
    lang_context_section = ""
    if lang.llm_context:
        ctx_tmpl = prompts.get("optimize_system_lang_context", "Language context: {llm_context}")
        lang_context_section = ctx_tmpl.format(llm_context=lang.llm_context)
    system = prompts["optimize_system"].format(
        display_name=lang.display_name,
        fence=lang.fence,
        lang_context_section=lang_context_section,
    )
    if target_context:
        if provider == "anthropic":
            prefix = f"<target_architecture>\n{target_context}\n</target_architecture>\n\n"
        else:
            prefix = f"## Target Architecture\n{target_context}\n\n"
        system = prefix + system
    return system


def make_optimize_system_prompt(lang: "LanguageSpec", provider: str = "openai") -> str:
    """Build the optimization system prompt for *lang* and *provider*."""
    return _make_optimize_system(lang, provider)


# ---------------------------------------------------------------------------
# OpenAI client helpers
# ---------------------------------------------------------------------------

def _make_openai_client(api_key: str | None, base_url: str | None) -> OpenAI:
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Anthropic client helpers
# ---------------------------------------------------------------------------

def _make_anthropic_client(api_key: str | None, base_url: str | None):
    try:
        import anthropic as _anthropic
    except ImportError as e:
        raise LLMConnectionError(
            "anthropic package not installed. Run: pip install anthropic"
        ) from e
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return _anthropic.Anthropic(**kwargs)


def _anthropic_thinking_params(model: str, think: bool) -> tuple[dict | None, int]:
    """Return (thinking_config_or_None, max_tokens) for an Anthropic request."""
    if not think:
        return None, 8192
    # Adaptive thinking for Opus 4+ (budget managed by the model)
    if "opus-4" in model or ("opus" in model and any(v in model for v in ("4-5", "4-6", "4.5", "4.6"))):
        return {"type": "adaptive"}, 32000
    # Manual budget_tokens for Sonnet/Haiku and older Opus
    return {"type": "enabled", "budget_tokens": 10000}, 20000


def _anthropic_error_map(exc) -> Exception:
    """Map anthropic exceptions to perf-agent typed errors."""
    try:
        import anthropic as _anthropic
        if isinstance(exc, _anthropic.AuthenticationError):
            return LLMConnectionError("Invalid Anthropic API key.")
        if isinstance(exc, _anthropic.APIConnectionError):
            return LLMConnectionError(f"Cannot connect to Anthropic API: {exc}")
        if isinstance(exc, _anthropic.NotFoundError):
            return LLMModelNotFoundError(f"Claude model not found: {exc}")
        if isinstance(exc, _anthropic.APIStatusError):
            return LLMConnectionError(f"Anthropic API error {exc.status_code}: {exc.message}")
    except ImportError:
        pass
    return LLMConnectionError(f"Anthropic error: {exc}")


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def split_thinking(text: str) -> tuple[str, str]:
    """Extract <think>...</think> blocks used by Ollama/local models.

    Returns (thinking_content, clean_text).  For native Anthropic thinking
    blocks, callers extract the thinking from content blocks directly.
    """
    thinking_parts = _THINK_RE.findall(text)
    thinking_text = "\n\n".join(p.strip() for p in thinking_parts)
    clean_text = _THINK_RE.sub("", text).strip()
    return thinking_text, clean_text


def extract_code_block(response: str, lang: "LanguageSpec") -> str:
    """Extract the first ```<lang.fence> ... ``` block from *response*.

    Tries the exact language fence first, falls back to any fenced block.
    Raises NoCodeBlockError if nothing found.
    """
    pattern = re.compile(rf"```{re.escape(lang.fence)}\s*\n(.*?)```", re.DOTALL)
    m = pattern.search(response)
    if m:
        return m.group(1)
    m = re.search(r"```\w*\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1)
    raise NoCodeBlockError(f"LLM response contained no ```{lang.fence} code block")


def extract_change_summary(response: str) -> str:
    """Return CHANGE: line content, or fall back to first non-empty line."""
    m = _CHANGE_LINE_RE.search(response)
    if m:
        return m.group(1).strip()
    for line in response.splitlines():
        line = line.strip()
        if line and not line.startswith("```"):
            return line[:120]
    return "(no description)"


# ---------------------------------------------------------------------------
# User message builders (shared across providers)
# ---------------------------------------------------------------------------

def build_user_message(
    binary: str,
    metrics: StatMetrics,
    functions: list[HotFunction],
) -> str:
    def _fmt_int(v: int | None) -> str:
        return f"{v:,}" if v is not None else "N/A"

    def _fmt_float(v: float | None, precision: int = 2) -> str:
        return f"{v:.{precision}f}" if v is not None else "N/A"

    hotfuncs_lines: list[str] = []
    for f in functions[:20]:
        hotfuncs_lines.append(
            f"  {f.overhead_pct:6.2f}%  {f.samples:6d}  {f.symbol}  [{f.dso}]"
        )
    hotfuncs_block = "\n".join(hotfuncs_lines) if hotfuncs_lines else "  (no data)"

    return f"""\
Binary: {binary}
Runtime: {_fmt_float(metrics.elapsed_seconds)}s | IPC: {_fmt_float(metrics.ipc)} | Cycles: {_fmt_int(metrics.cycles)}

=== Hardware Counters ===
Instructions:     {_fmt_int(metrics.instructions)}
Task clock:       {_fmt_float(metrics.task_clock_ms, 1)} ms
CPUs utilized:    {_fmt_float(metrics.cpu_utilized)}
Branches:         {_fmt_int(metrics.branches)} ({_fmt_float(metrics.branch_miss_pct, 1)}% mispredicted)
Cache references: {_fmt_int(metrics.cache_references)} ({_fmt_float(metrics.cache_miss_pct, 1)}% misses)

=== Top Hot Functions (perf report) ===
{hotfuncs_block}

Identify the main performance bottlenecks and provide 3-5 specific,
actionable recommendations to improve performance.
"""


def build_optimize_user_message(
    binary: str,
    current_source: str,
    metrics: StatMetrics,
    functions: list[HotFunction],
    history: list["IterationRecord"],
    iteration: int,
    max_iterations: int,
    lang: "LanguageSpec",
    provider: str = "openai",
) -> str:
    def _fmt_int(v: int | None) -> str:
        return f"{v:,}" if v is not None else "N/A"

    def _fmt_float(v: float | None, precision: int = 2) -> str:
        return f"{v:.{precision}f}" if v is not None else "N/A"

    hotfuncs_lines: list[str] = []
    for f in functions[:20]:
        hotfuncs_lines.append(
            f"  {f.overhead_pct:6.2f}%  {f.samples:6d}  {f.symbol}  [{f.dso}]"
        )
    hotfuncs_block = "\n".join(hotfuncs_lines) if hotfuncs_lines else "  (no data)"

    if history:
        rows = ["  Iter  Result          Delta    Description"]
        for rec in history:
            if rec.compile_failed:
                result_str, delta_str = "COMPILE FAIL  ", "    N/A"
            elif rec.user_rejected:
                result_str, delta_str = "USER REJECTED  ", "    N/A"
                desc = rec.description + (f"  [Feedback: {rec.user_feedback}]" if rec.user_feedback else "")
                rows.append(f"  {rec.iteration:4d}  {result_str}  {delta_str}  {desc}")
                continue
            elif rec.kept:
                result_str = "KEPT          "
                delta_str = f"{rec.delta_pct:+7.1f}%"
            else:
                result_str = "REJECTED      "
                delta_str = f"{rec.delta_pct:+7.1f}%"
            rows.append(f"  {rec.iteration:4d}  {result_str}  {delta_str}  {rec.description}")
        history_block = "\n".join(rows)
    else:
        history_block = "  (no previous attempts)"

    source_block = current_source[:_MAX_REPORT_CHARS]
    if len(current_source) > _MAX_REPORT_CHARS:
        source_block += "\n... (truncated)"

    prompts = _load_prompts(provider)
    source_label = prompts.get("optimize_user_source_label", "=== Current Source Code ===")

    return f"""\
=== OPTIMIZATION REQUEST — Iteration {iteration}/{max_iterations} ===
Binary: {binary} | elapsed: {_fmt_float(metrics.elapsed_seconds)}s | IPC: {_fmt_float(metrics.ipc)} | Cache miss: {_fmt_float(metrics.cache_miss_pct, 1)}%

=== Current Hardware Counters ===
Instructions:     {_fmt_int(metrics.instructions)}
Task clock:       {_fmt_float(metrics.task_clock_ms, 1)} ms
CPUs utilized:    {_fmt_float(metrics.cpu_utilized)}
Branches:         {_fmt_int(metrics.branches)} ({_fmt_float(metrics.branch_miss_pct, 1)}% mispredicted)
Cache references: {_fmt_int(metrics.cache_references)} ({_fmt_float(metrics.cache_miss_pct, 1)}% misses)

=== Top Hot Functions ===
{hotfuncs_block}

=== Optimization History ===
{history_block}

{source_label}
```{lang.fence}
{source_block}
```

Propose exactly one optimization. Output full modified source in a ```{lang.fence} block.
"""


# ---------------------------------------------------------------------------
# collect_optimization — OpenAI path
# ---------------------------------------------------------------------------

def _collect_optimization_openai(
    current_source: str,
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    history: list["IterationRecord"],
    iteration: int,
    max_iterations: int,
    lang: "LanguageSpec",
    model: str,
    base_url: str | None,
    api_key: str | None,
    think: bool,
    target_context: str | None,
) -> tuple[str, str, str]:
    system_content = _make_optimize_system(lang, "openai", target_context)
    user_content = build_optimize_user_message(
        binary, current_source, metrics, functions,
        history, iteration, max_iterations, lang, provider="openai",
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
    client = _make_openai_client(api_key, base_url)
    try:
        stream = client.chat.completions.create(model=model, messages=messages, stream=True)
        full_response = "".join(chunk.choices[0].delta.content or "" for chunk in stream)
    except _openai.AuthenticationError as e:
        raise LLMConnectionError("Invalid OpenAI API key.") from e
    except _openai.APIConnectionError as e:
        raise LLMConnectionError(f"Cannot connect to LLM API: {e}") from e
    except _openai.NotFoundError as e:
        raise LLMModelNotFoundError(f"Model '{model}' not found.") from e

    thinking_text, response_text = split_thinking(full_response)
    return thinking_text, response_text, extract_change_summary(response_text)


# ---------------------------------------------------------------------------
# collect_optimization — Anthropic path
# ---------------------------------------------------------------------------

def _collect_optimization_anthropic(
    current_source: str,
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    history: list["IterationRecord"],
    iteration: int,
    max_iterations: int,
    lang: "LanguageSpec",
    model: str,
    base_url: str | None,
    api_key: str | None,
    think: bool,
    target_context: str | None,
) -> tuple[str, str, str]:
    try:
        import anthropic as _anthropic
    except ImportError as e:
        raise LLMConnectionError("anthropic package not installed. Run: pip install anthropic") from e

    system_content = _make_optimize_system(lang, "anthropic", target_context)
    user_content = build_optimize_user_message(
        binary, current_source, metrics, functions,
        history, iteration, max_iterations, lang, provider="anthropic",
    )
    thinking_config, max_tokens = _anthropic_thinking_params(model, think)

    client = _make_anthropic_client(api_key, base_url)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_content,
        "messages": [{"role": "user", "content": user_content}],
    }
    if thinking_config:
        kwargs["thinking"] = thinking_config

    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        raise _anthropic_error_map(exc) from exc

    # Extract thinking and text from typed content blocks
    thinking_parts: list[str] = []
    text_parts: list[str] = []
    for block in response.content:
        if block.type == "thinking":
            thinking_parts.append(block.thinking)
        elif block.type == "text":
            text_parts.append(block.text)

    thinking_text = "\n\n".join(thinking_parts)
    response_text = "\n".join(text_parts)
    return thinking_text, response_text, extract_change_summary(response_text)


# ---------------------------------------------------------------------------
# Public: collect_optimization (dispatches by provider)
# ---------------------------------------------------------------------------

def collect_optimization(
    current_source: str,
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    history: list["IterationRecord"],
    iteration: int,
    max_iterations: int,
    lang: "LanguageSpec",
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str | None = None,
    think: bool = True,
    target_context: str | None = None,
) -> tuple[str, str, str]:
    """Request one optimization from the LLM. Returns (thinking_text, response_text, change_summary)."""
    if detect_provider(model) == "anthropic":
        return _collect_optimization_anthropic(
            current_source, metrics, functions, binary, history,
            iteration, max_iterations, lang, model, base_url, api_key, think, target_context,
        )
    return _collect_optimization_openai(
        current_source, metrics, functions, binary, history,
        iteration, max_iterations, lang, model, base_url, api_key, think, target_context,
    )


# ---------------------------------------------------------------------------
# collect_security_remediation
# ---------------------------------------------------------------------------

def collect_security_remediation(
    current_source: str,
    issues: list[str],
    lang: "LanguageSpec",
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str | None = None,
) -> str | None:
    """Ask the LLM to fix all *issues* in *current_source*.

    Returns the fixed source string, or None on any failure.
    """
    provider = detect_provider(model)
    prompts = _load_prompts(provider)
    system = prompts["remediation_system"].format(
        display_name=lang.display_name,
        fence=lang.fence,
    )
    issue_list = "\n".join(f"- {issue}" for issue in issues)

    if provider == "anthropic":
        user_msg = (
            f"The following security issues were detected in this "
            f"{lang.display_name} program:\n\n"
            f"<issues>\n{issue_list}\n</issues>\n\n"
            f"Fix all of them and output the corrected code:\n\n"
            f"```{lang.fence}\n{current_source}\n```"
        )
        try:
            import anthropic as _anthropic
        except ImportError as e:
            raise LLMConnectionError("anthropic package not installed.") from e
        client = _make_anthropic_client(api_key, base_url)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = "\n".join(b.text for b in response.content if b.type == "text")
        except Exception:
            return None
    else:
        user_msg = (
            f"The following security issues were detected in this "
            f"{lang.display_name} program:\n\n"
            f"{issue_list}\n\n"
            f"Fix all of them:\n\n"
            f"```{lang.fence}\n{current_source}\n```"
        )
        client = _make_openai_client(api_key, base_url)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                stream=False,
            )
            text = response.choices[0].message.content or ""
        except (_openai.AuthenticationError, _openai.APIConnectionError,
                _openai.NotFoundError, _openai.APIError):
            return None

    try:
        return extract_code_block(text, lang)
    except NoCodeBlockError:
        return None


# ---------------------------------------------------------------------------
# stream_analysis — OpenAI path
# ---------------------------------------------------------------------------

def _stream_analysis_openai(
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    target_context: str | None,
    lang: "LanguageSpec | None",
) -> Iterator[tuple[str, bool]]:
    prompts = _load_prompts("openai")
    analysis_system = prompts["analysis_system"]
    if lang and lang.llm_context:
        analysis_system = f"## Language context\n{lang.llm_context}\n\n" + analysis_system
    if target_context:
        analysis_system = f"## Target Architecture\n{target_context}\n\n" + analysis_system

    messages = [
        {"role": "system", "content": analysis_system},
        {"role": "user", "content": build_user_message(binary, metrics, functions)},
    ]
    client = _make_openai_client(api_key, base_url)
    try:
        stream = client.chat.completions.create(model=model, messages=messages, stream=True)
    except _openai.AuthenticationError as e:
        raise LLMConnectionError("Invalid OpenAI API key.") from e
    except _openai.APIConnectionError as e:
        raise LLMConnectionError(f"Cannot connect to LLM API: {e}") from e
    except _openai.NotFoundError as e:
        raise LLMModelNotFoundError(f"Model '{model}' not found.") from e

    in_think = False
    try:
        for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            if not content:
                continue
            i = 0
            while i < len(content):
                if not in_think:
                    think_start = content.find("<think>", i)
                    if think_start == -1:
                        if content[i:]:
                            yield (content[i:], False)
                        break
                    else:
                        if content[i:think_start]:
                            yield (content[i:think_start], False)
                        in_think = True
                        i = think_start + len("<think>")
                else:
                    think_end = content.find("</think>", i)
                    if think_end == -1:
                        if content[i:]:
                            yield (content[i:], True)
                        break
                    else:
                        if content[i:think_end]:
                            yield (content[i:think_end], True)
                        in_think = False
                        i = think_end + len("</think>")
    except _openai.AuthenticationError as e:
        raise LLMConnectionError("Invalid OpenAI API key.") from e
    except _openai.APIConnectionError as e:
        raise LLMConnectionError(f"Cannot connect to LLM API: {e}") from e
    except _openai.NotFoundError as e:
        raise LLMModelNotFoundError(f"Model '{model}' not found.") from e


# ---------------------------------------------------------------------------
# stream_analysis — Anthropic path
# ---------------------------------------------------------------------------

def _stream_analysis_anthropic(
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    think: bool,
    target_context: str | None,
    lang: "LanguageSpec | None",
) -> Iterator[tuple[str, bool]]:
    try:
        import anthropic as _anthropic
    except ImportError as e:
        raise LLMConnectionError("anthropic package not installed.") from e

    prompts = _load_prompts("anthropic")
    analysis_system = prompts["analysis_system"]
    if lang and lang.llm_context:
        analysis_system = f"<language_context>\n{lang.llm_context}\n</language_context>\n\n" + analysis_system
    if target_context:
        analysis_system = f"<target_architecture>\n{target_context}\n</target_architecture>\n\n" + analysis_system

    thinking_config, max_tokens = _anthropic_thinking_params(model, think)
    client = _make_anthropic_client(api_key, base_url)

    stream_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": analysis_system,
        "messages": [{"role": "user", "content": build_user_message(binary, metrics, functions)}],
    }
    if thinking_config:
        stream_kwargs["thinking"] = thinking_config

    try:
        with client.messages.stream(**stream_kwargs) as stream:
            in_thinking = False
            for event in stream.events():
                etype = event.type
                if etype == "content_block_start":
                    in_thinking = getattr(event.content_block, "type", "") == "thinking"
                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", "")
                    if dtype == "thinking_delta":
                        yield (delta.thinking, True)
                    elif dtype == "text_delta":
                        yield (delta.text, False)
                elif etype == "content_block_stop":
                    in_thinking = False
    except Exception as exc:
        raise _anthropic_error_map(exc) from exc


# ---------------------------------------------------------------------------
# Public: stream_analysis (dispatches by provider)
# ---------------------------------------------------------------------------

def stream_analysis(
    metrics: StatMetrics,
    functions: list[HotFunction],
    binary: str,
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str | None = None,
    think: bool = True,
    target_context: str | None = None,
    lang: "LanguageSpec | None" = None,
) -> Iterator[tuple[str, bool]]:
    """Stream LLM analysis tokens. Yields (chunk, is_thinking) tuples."""
    if detect_provider(model) == "anthropic":
        yield from _stream_analysis_anthropic(
            metrics, functions, binary, model, base_url, api_key, think, target_context, lang
        )
    else:
        yield from _stream_analysis_openai(
            metrics, functions, binary, model, base_url, api_key, target_context, lang
        )
