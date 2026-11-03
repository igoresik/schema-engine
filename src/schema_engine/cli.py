"""Command-line entry point: ``schema-engine``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from schema_engine import __version__
from schema_engine.io import UnsupportedFormatError, load_dataset
from schema_engine.privacy import PrivacyMode
from schema_engine.profile import build_profile
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


if __name__ == "__main__":  # pragma: no cover
    app()
