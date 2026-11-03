"""Command-line entry point: ``schema-engine``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from schema_engine import __version__
from schema_engine.config import ConfigError, load_settings
from schema_engine.io import UnsupportedFormatError, load_dataset
from schema_engine.privacy import PrivacyMode
from schema_engine.profile import build_profile
from schema_engine.providers import create_provider
from schema_engine.samples import write_samples

app = typer.Typer(
    help="Cost-aware AI ETL: LLM-written Pandas transforms from metadata only.",
    no_args_is_help=True,
    add_completion=False,
)


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
    sheet_ref: str | int = int(sheet) if sheet.isdigit() else sheet
    try:
        df = load_dataset(path, sheet=sheet_ref)
    except UnsupportedFormatError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
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


if __name__ == "__main__":  # pragma: no cover
    app()
