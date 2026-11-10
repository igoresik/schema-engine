"""Command-line entry point: ``schema-engine``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import pandas as pd
import typer

from schema_engine import __version__
from schema_engine.config import ConfigError, load_settings
from schema_engine.engine import (
    Attempt,
    Lost,
    compile_transform,
    lost_values,
    preset_error,
    transform_script,
)
from schema_engine.io import (
    WRITE_SUFFIXES,
    default_output,
    load_dataset,
    save_dataset,
)
from schema_engine.presets import EXPORT_PRESETS, ExportPreset
from schema_engine.privacy import PrivacyMode
from schema_engine.profile import build_profile
from schema_engine.providers import ProviderError, create_provider
from schema_engine.samples import write_samples
from schema_engine.sandbox import run_transform

app = typer.Typer(
    help="Cost-aware AI ETL: LLM-written Pandas transforms from metadata only.",
    no_args_is_help=True,
    add_completion=False,
)

_PRESET_HELP = f"Target format the output must fit: {', '.join(EXPORT_PRESETS)}."


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """schema-engine command group."""


@app.command()
def profile(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    privacy_mode: Annotated[
        PrivacyMode,
        typer.Option("--privacy-mode", "-p", help="What row-level data the LLM may see."),
    ] = PrivacyMode.MASKED,
    sheet: Annotated[str, typer.Option(help="Excel sheet name or index.")] = "0",
    compact: Annotated[bool, typer.Option(help="Print single-line JSON.")] = False,
) -> None:
    """Show exactly the metadata that would be sent to the LLM for PATH."""
    df = _load(path, sheet)
    result = build_profile(df, source=path.name, privacy_mode=privacy_mode)
    typer.echo(result.to_json(indent=None if compact else 2))
    typer.secho(
        f"~{result.estimated_tokens()} tokens for {result.row_count} rows "
        f"(privacy mode: {result.privacy_mode})",
        fg=typer.colors.CYAN,
        err=True,
    )


@app.command()
def samples(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write sample files into.")] = Path(
        "samples"
    ),
    rows: Annotated[int, typer.Option(min=1)] = 50,
    seed: Annotated[int, typer.Option()] = 7,
) -> None:
    """Generate synthetic dirty CSV/XLSX files for testing."""
    for written in write_samples(out_dir, rows=rows, seed=seed):
        typer.echo(str(written))


@app.command("test-connection")
def test_connection(
    provider: Annotated[str | None, typer.Option(help="ollama, openai or groq.")] = None,
    model: Annotated[
        str | None, typer.Option(help="Model name. Default depends on provider.")
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="Override the endpoint URL.")] = None,
) -> None:
    """Send a one-token request to check the endpoint, API key and model."""
    try:
        settings = load_settings(provider=provider, model=model, base_url=base_url)
        llm = create_provider(settings)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    for key, value in settings.redacted().items():
        typer.echo(f"{key}: {value}")
    try:
        report = llm.health_check()
    finally:
        llm.close()
    if not report.ok:
        typer.secho(f"failed: {report.detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(
        f"ok: {report.latency_ms:.0f} ms, {report.usage.total_tokens} tokens used",
        fg=typer.colors.GREEN,
    )
    limits = report.rate_limits
    if limits.remaining_requests is not None or limits.remaining_tokens is not None:
        typer.echo(
            f"rate limit left: {limits.remaining_requests} requests, "
            f"{limits.remaining_tokens} tokens"
        )


@app.command()
def run(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    instruction: Annotated[str, typer.Argument(help="What to do with the data, in plain words.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Cleaned data file. Default: NAME_clean next to PATH."),
    ] = None,
    save: Annotated[
        Path | None,
        typer.Option(help="Where to save the transform. Default: NAME_transform.py next to PATH."),
    ] = None,
    privacy_mode: Annotated[
        PrivacyMode,
        typer.Option("--privacy-mode", "-p", help="What row-level data the LLM may see."),
    ] = PrivacyMode.MASKED,
    sheet: Annotated[str, typer.Option(help="Excel sheet name or index.")] = "0",
    provider: Annotated[str | None, typer.Option(help="ollama, openai or groq.")] = None,
    model: Annotated[
        str | None, typer.Option(help="Model name. Default depends on provider.")
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="Override the endpoint URL.")] = None,
    attempts: Annotated[
        int, typer.Option(min=1, max=10, help="Max tries, counting self-healing retries.")
    ] = 3,
    timeout: Annotated[float, typer.Option(min=1, help="Seconds the transform may run.")] = 30.0,
    preset: Annotated[str | None, typer.Option(help=_PRESET_HELP)] = None,
) -> None:
    """Have the LLM write a transform for PATH, run it in the sandbox, and save both."""
    target = _preset(preset)
    output = output or default_output(path, target)
    save = save or path.with_name(f"{path.stem}_transform.py")
    _check_writable(output, target)
    df = _load(path, sheet)
    try:
        settings = load_settings(provider=provider, model=model, base_url=base_url)
        llm = create_provider(settings)
    except ConfigError as exc:
        _fail(str(exc), code=2)

    def report(attempt: Attempt) -> None:
        status = "ok" if attempt.error is None else f"failed, {_last_line(attempt.error)}"
        typer.secho(
            f"attempt {attempt.number}: {status} "
            f"({attempt.usage.total_tokens} tokens, {attempt.latency_ms:.0f} ms)",
            fg=typer.colors.GREEN if attempt.error is None else typer.colors.YELLOW,
            err=True,
        )

    try:
        result = compile_transform(
            df,
            instruction,
            llm,
            privacy_mode=privacy_mode,
            source=path.name,
            max_attempts=attempts,
            timeout=timeout,
            temperature=settings.preset.temperature,
            preset=target,
            on_attempt=report,
        )
    except ProviderError as exc:
        _fail(f"{settings.provider} request failed: {exc}")
    finally:
        llm.close()

    usage = result.usage
    typer.secho(
        f"{usage.total_tokens} tokens in total "
        f"({usage.prompt_tokens} prompt, {usage.completion_tokens} completion)",
        fg=typer.colors.CYAN,
        err=True,
    )
    if result.output is None:
        last = result.attempts[-1].error
        _fail(f"No working transform after {len(result.attempts)} attempts. Last error:\n{last}")
    if result.lost:
        _warn_lost(result.lost, f"No try kept every value, so attempt {result.picked + 1} is kept.")
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_text(
        transform_script(result, path, save, instruction, settings, target), encoding="utf-8"
    )
    _save(result.output, output, saved=save)
    typer.echo(f"{len(result.output)} rows -> {output}")
    typer.echo(f"transform -> {save}")


@app.command()
def apply(
    script: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Cleaned data file. Default: NAME_clean next to PATH."),
    ] = None,
    sheet: Annotated[str, typer.Option(help="Excel sheet name or index.")] = "0",
    timeout: Annotated[float, typer.Option(min=1, help="Seconds the transform may run.")] = 30.0,
    preset: Annotated[str | None, typer.Option(help=_PRESET_HELP)] = None,
) -> None:
    """Run a saved transform on PATH in the sandbox. No LLM, no tokens."""
    target = _preset(preset)
    output = output or default_output(path, target)
    _check_writable(output, target)
    df = _load(path, sheet)
    result = run_transform(script.read_text(encoding="utf-8"), df, timeout=timeout)
    if result.output is None:
        _fail(result.error or "transform failed")
    cleaned, problems = target.fit(result.output) if target else (result.output, [])
    if target and problems:
        _fail(preset_error(target.name, problems, cleaned.columns, lambda text: text))
    if lost := lost_values(df, result.output):
        _warn_lost(lost, "The transform may not handle every format in this file.")
    _save(cleaned, output)
    typer.echo(f"{len(cleaned)} rows -> {output}")


def _warn_lost(lost: list[Lost], why: str) -> None:
    lines = "\n".join(f"  '{x.column}': {x.blanked} of {x.filled} values" for x in lost)
    typer.secho(
        f"warning: values became blank. {why}\n{lines}\nCheck the output before you use it.",
        fg=typer.colors.YELLOW,
        err=True,
    )


def _save(df: pd.DataFrame, output: Path, *, saved: Path | None = None) -> None:
    try:
        save_dataset(df, output)
    except ValueError as exc:
        # e.g. SQLite sees "Name" and "name" as the same column.
        _fail(f"{exc}\ntransform -> {saved}" if saved else str(exc))


def _load(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return load_dataset(path, sheet=int(sheet) if sheet.isdigit() else sheet)
    except ValueError as exc:  # UnsupportedFormatError, empty CSV, broken workbook
        _fail(str(exc), code=2)


def _fail(message: str, *, code: int = 1) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _preset(name: str | None) -> ExportPreset | None:
    if name is None:
        return None
    if name.lower() not in EXPORT_PRESETS:
        _fail(f"Unknown preset '{name}'. Choose one of: {', '.join(EXPORT_PRESETS)}.", code=2)
    return EXPORT_PRESETS[name.lower()]


def _check_writable(output: Path, target: ExportPreset | None = None) -> None:
    """Fail before spending tokens on a file type that can't be written."""
    suffix = output.suffix.lower()
    if target and suffix != target.suffix:
        _fail(f"The {target.name} preset writes {target.suffix} files, not '{suffix}'.", code=2)
    if suffix not in WRITE_SUFFIXES:
        _fail(f"Can't write '{output.suffix}' files. Use .csv, .tsv, .xlsx or .sql.", code=2)


def _last_line(error: str) -> str:
    return error.strip().splitlines()[-1]


if __name__ == "__main__":  # pragma: no cover
    app()
