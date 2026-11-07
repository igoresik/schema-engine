"""Local HTTP API behind the desktop window. It only listens on 127.0.0.1."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pandas.api.types import is_datetime64_any_dtype
from pydantic import BaseModel, Field

from schema_engine import __version__
from schema_engine.app import DATA_DIR
from schema_engine.config import (
    DEFAULT_PROVIDER,
    PRESETS,
    ConfigError,
    Settings,
    load_settings,
    redact,
    save_key,
    stored_key,
    update_config,
)
from schema_engine.engine import (
    Attempt,
    CompileResult,
    Lost,
    compile_transform,
    lost_values,
    preset_error,
    transform_script,
)
from schema_engine.io import (
    EXCEL_SUFFIXES,
    WRITE_SUFFIXES,
    UnsupportedFormatError,
    default_output,
    load_dataset,
    save_dataset,
)
from schema_engine.presets import EXPORT_PRESETS, ExportPreset
from schema_engine.privacy import PrivacyMode
from schema_engine.profile import build_profile
from schema_engine.providers import LLMProvider, ProviderError, Usage, create_provider
from schema_engine.samples import write_samples
from schema_engine.sandbox import run_transform

PREVIEW_ROWS = 100
STATIC = Path(__file__).parent / "static"


class Dialogs(Protocol):
    """Native file dialogs. Only the desktop window has them."""

    def open(self, kind: Literal["data", "transform"]) -> str | None: ...

    def save(self, kind: Literal["data", "transform"], suggested: Path) -> str | None: ...


@dataclass
class Dataset:
    id: str
    path: Path
    df: pd.DataFrame
    sheet: str | int
    sheets: list[str]
    dropped: bool
    """Dropped into the window, so ``path`` is a copy in the data folder."""


@dataclass
class Output:
    id: str
    dataset: Dataset
    df: pd.DataFrame
    code: str
    preset: ExportPreset | None
    lost: list[Lost]
    script: str
    """What "save transform" writes: the code with a header, like the CLI's."""


@dataclass
class Run:
    id: str
    status: Literal["running", "done", "failed"] = "running"
    attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None
    output: Output | None = None
    result: CompileResult | None = None


class RunIn(BaseModel):
    file: str
    instruction: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    privacy: PrivacyMode = PrivacyMode.MASKED
    preset: str | None = None
    attempts: int = Field(3, ge=1, le=10)
    timeout: float = Field(30.0, ge=1)


class ApplyIn(BaseModel):
    file: str
    code: str | None = None
    path: str | None = None
    preset: str | None = None
    timeout: float = Field(30.0, ge=1)


class OpenIn(BaseModel):
    path: str | None = None
    sheet: str = "0"


class SaveIn(BaseModel):
    kind: Literal["data", "transform"]
    path: str | None = None


class ProviderIn(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    """Left out keeps the saved key. Empty forgets it."""


class DefaultIn(BaseModel):
    provider: str


def create_app(
    dialogs: Dialogs | None = None,
    provider_factory: Callable[[Settings], LLMProvider] = create_provider,
    config_file: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="schema-engine", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    datasets: dict[str, Dataset] = {}
    outputs: dict[str, Output] = {}
    runs: dict[str, Run] = {}

    @app.middleware("http")
    async def same_site_only(request: Request, call_next):
        # Other sites open in a browser could otherwise post to this port.
        origin = request.headers.get("origin")
        own = f"{request.url.scheme}://{request.url.netloc}"
        cross = request.headers.get("sec-fetch-site") in ("cross-site", "same-site")
        if request.method != "GET" and ((origin and origin != own) or cross):
            return JSONResponse({"detail": "Requests from other sites are blocked."}, 403)
        return await call_next(request)

    def settings(provider: str | None = None, model: str | None = None) -> Settings:
        # The app runs from its install folder, so a .env there is never meant for it.
        try:
            return load_settings(
                provider=provider, model=model, dotenv_path=None, config_file=config_file
            )
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

    def dataset(file_id: str) -> Dataset:
        if file_id not in datasets:
            raise HTTPException(404, "That file isn't open any more. Open it again.")
        return datasets[file_id]

    def open_dataset(path: Path, sheet: str = "0", *, dropped: bool = False) -> dict[str, Any]:
        picked: str | int = int(sheet) if sheet.isdigit() else sheet
        try:
            df = load_dataset(path, sheet=picked)
        except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        sheets = []
        if path.suffix.lower() in EXCEL_SUFFIXES:
            with pd.ExcelFile(path) as book:
                sheets = [str(name) for name in book.sheet_names]
        item = Dataset(uuid.uuid4().hex, path, df, picked, sheets, dropped)
        datasets[item.id] = item
        return dataset_view(item)

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "desktop": dialogs is not None,
            "presets": [{"name": p.name, "suffix": p.suffix} for p in EXPORT_PRESETS.values()],
            "privacy_modes": [m.value for m in PrivacyMode],
        }

    @app.post("/api/files/open")
    def open_file(body: OpenIn) -> dict[str, Any] | None:
        path = body.path
        if path is None:
            if dialogs is None:
                raise HTTPException(400, "No path given.")
            path = dialogs.open("data")
            if path is None:
                return None
        return open_dataset(Path(path), body.sheet)

    @app.post("/api/files/drop")
    async def drop_file(request: Request, name: str) -> dict[str, Any]:
        if not Path(name).name:
            raise HTTPException(400, "The file needs a name.")
        folder = DATA_DIR / "dropped" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / Path(name).name
        path.write_bytes(await request.body())
        return open_dataset(path, dropped=True)

    @app.post("/api/files/sample")
    def sample_file() -> dict[str, Any]:
        csv_path, _ = write_samples(DATA_DIR / "samples")
        return open_dataset(csv_path)

    @app.get("/api/files/{file_id}/profile")
    def profile(file_id: str, privacy: PrivacyMode = PrivacyMode.MASKED) -> dict[str, Any]:
        item = dataset(file_id)
        result = build_profile(item.df, source=item.path.name, privacy_mode=privacy)
        return {"profile": json.loads(result.to_json()), "tokens": result.estimated_tokens()}

    @app.post("/api/runs")
    def start_run(body: RunIn) -> dict[str, str]:
        item = dataset(body.file)
        target = preset(body.preset)
        resolved = settings(body.provider, body.model)
        try:
            llm = provider_factory(resolved)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        run = Run(uuid.uuid4().hex)
        runs[run.id] = run

        def work() -> None:
            try:
                result = compile_transform(
                    item.df,
                    body.instruction,
                    llm,
                    privacy_mode=body.privacy,
                    source=item.path.name,
                    max_attempts=body.attempts,
                    timeout=body.timeout,
                    temperature=resolved.preset.temperature,
                    preset=target,
                    on_attempt=run.attempts.append,
                )
            except ProviderError as exc:
                run.error, run.status = f"{resolved.provider} request failed: {exc}", "failed"
                return
            finally:
                llm.close()
            run.result = result
            if result.output is None:
                run.error = f"No working transform after {len(result.attempts)} tries."
                run.status = "failed"
                return
            name = item.path.with_name(f"{item.path.stem}_transform.py")
            script = transform_script(result, item.path, name, body.instruction, resolved, target)
            run.output = keep(item, result.output, result.code or "", target, result.lost, script)
            run.status = "done"

        threading.Thread(target=work, daemon=True).start()
        return {"id": run.id}

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        if run_id not in runs:
            raise HTTPException(404, "Unknown run.")
        run = runs[run_id]
        usage = run.result.usage if run.result else sum((a.usage for a in run.attempts), Usage())
        return {
            "id": run.id,
            "status": run.status,
            "error": run.error,
            "attempts": [attempt_view(a) for a in list(run.attempts)],
            "usage": {
                "prompt": usage.prompt_tokens,
                "completion": usage.completion_tokens,
                "total": usage.total_tokens,
            },
            "output": output_view(run.output) if run.output else None,
        }

    @app.post("/api/apply")
    def apply(body: ApplyIn) -> dict[str, Any] | None:
        item = dataset(body.file)
        target = preset(body.preset)
        code = body.code
        if code is None:
            path = body.path or (dialogs.open("transform") if dialogs else None)
            if path is None:
                if dialogs is None:
                    raise HTTPException(400, "No transform given.")
                return None
            code = Path(path).read_text(encoding="utf-8")
        result = run_transform(code, item.df, timeout=body.timeout)
        if result.output is None:
            raise HTTPException(422, result.error or "The transform failed.")
        cleaned, problems = target.fit(result.output) if target else (result.output, [])
        if target and problems:
            raise HTTPException(
                422, preset_error(target.name, problems, cleaned.columns, lambda text: text)
            )
        lost = lost_values(item.df, result.output)
        return output_view(keep(item, cleaned, code, target, lost, code))

    @app.post("/api/outputs/{output_id}/save")
    def save(output_id: str, body: SaveIn) -> dict[str, str] | None:
        if output_id not in outputs:
            raise HTTPException(404, "Unknown result.")
        out = outputs[output_id]
        suggested = suggested_path(out, body.kind)
        chosen = body.path or (dialogs.save(body.kind, suggested) if dialogs else str(suggested))
        if chosen is None:
            return None
        path = Path(chosen)
        if body.kind == "transform":
            path.write_text(out.script, encoding="utf-8")
            return {"path": str(path)}
        suffix = path.suffix.lower()
        if out.preset and suffix != out.preset.suffix:
            raise HTTPException(
                400, f"The {out.preset.name} format is saved as {out.preset.suffix}, not {suffix}."
            )
        if suffix not in WRITE_SUFFIXES:
            raise HTTPException(400, f"Can't save {suffix} files. Use .csv, .tsv, .xlsx or .sql.")
        try:
            save_dataset(out.df, path)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": str(path)}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return {
            "default": settings().provider,
            "providers": [provider_view(name) for name in PRESETS],
        }

    @app.put("/api/settings")
    def set_default(body: DefaultIn) -> dict[str, Any]:
        settings(body.provider)  # rejects unknown names
        update_config(
            {"provider": None if body.provider == DEFAULT_PROVIDER else body.provider},
            path=config_file,
        )
        return get_settings()

    @app.put("/api/settings/{name}")
    def set_provider(name: str, body: ProviderIn) -> dict[str, Any]:
        settings(name)
        preset_of = PRESETS[name]
        update_config(
            {
                "model": None if body.model == preset_of.default_model else body.model,
                "base_url": None if body.base_url == preset_of.base_url else body.base_url,
            },
            provider=name,
            path=config_file,
        )
        if "api_key" in body.model_fields_set and preset_of.key_env:
            save_key(name, (body.api_key or "").strip() or None)
        return provider_view(name)

    @app.post("/api/settings/{name}/test")
    def test_provider(name: str) -> dict[str, Any]:
        try:
            llm = provider_factory(settings(name))
        except ConfigError as exc:
            return {"ok": False, "detail": str(exc)}
        try:
            report = llm.health_check()
        finally:
            llm.close()
        return {
            "ok": report.ok,
            "detail": report.detail,
            "latency_ms": report.latency_ms,
            "tokens": report.usage.total_tokens,
        }

    @app.get("/api/ollama")
    def ollama() -> dict[str, Any]:
        base = settings("ollama").base_url
        try:
            reply = httpx.get(f"{base}/api/tags", timeout=2)
            reply.raise_for_status()
        except httpx.HTTPError:
            return {"running": False, "models": []}
        return {"running": True, "models": [m["name"] for m in reply.json().get("models", [])]}

    def provider_view(name: str) -> dict[str, Any]:
        resolved = settings(name)
        preset_of = PRESETS[name]
        return {
            "name": name,
            "needs_key": preset_of.key_env is not None,
            "key_env": preset_of.key_env,
            "default_model": preset_of.default_model,
            "model": resolved.model,
            "default_base_url": preset_of.base_url,
            "base_url": resolved.base_url,
            "key": redact(resolved.api_key),
            "key_in_app": preset_of.key_env is not None and stored_key(name) is not None,
        }

    def keep(
        item: Dataset,
        df: pd.DataFrame,
        code: str,
        target: ExportPreset | None,
        lost: list[Lost],
        script: str,
    ) -> Output:
        out = Output(uuid.uuid4().hex, item, df, code, target, lost, script)
        outputs[out.id] = out
        return out

    if (STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app


def preset(name: str | None) -> ExportPreset | None:
    if not name:
        return None
    if name.lower() not in EXPORT_PRESETS:
        raise HTTPException(400, f"Unknown format '{name}'.")
    return EXPORT_PRESETS[name.lower()]


def suggested_path(out: Output, kind: str) -> Path:
    source = out.dataset.path
    if out.dataset.dropped:  # the copy lives in the data folder, so suggest Documents
        documents = Path.home() / "Documents"
        source = (documents if documents.is_dir() else Path.home()) / source.name
    if kind == "transform":
        return source.with_name(f"{source.stem}_transform.py")
    return default_output(source, out.preset)


def table(df: pd.DataFrame, rows: int = PREVIEW_ROWS) -> dict[str, Any]:
    head = df.head(rows).copy()
    for position in range(head.shape[1]):
        column = head.iloc[:, position]
        if is_datetime64_any_dtype(column):
            dates = (column.dropna() == column.dropna().dt.normalize()).all()
            head.isetitem(
                position, column.dt.strftime("%Y-%m-%d" if dates else "%Y-%m-%d %H:%M:%S")
            )
    data = json.loads(head.to_json(orient="values", default_handler=str))
    return {"columns": [str(c) for c in df.columns], "rows": data, "total": len(df)}


def dataset_view(item: Dataset) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.path.name,
        "path": None if item.dropped else str(item.path),
        "sheet": item.sheet,
        "sheets": item.sheets,
        "table": table(item.df),
    }


def output_view(out: Output) -> dict[str, Any]:
    return {
        "id": out.id,
        "table": table(out.df),
        "code": out.code,
        "lost": [{"column": x.column, "blanked": x.blanked, "filled": x.filled} for x in out.lost],
        "preset": out.preset.name if out.preset else None,
        "suggested": {
            "data": suggested_path(out, "data").name,
            "transform": suggested_path(out, "transform").name,
        },
    }


def attempt_view(attempt: Attempt) -> dict[str, Any]:
    return {
        "number": attempt.number,
        "ok": attempt.error is None,
        "summary": attempt.error.strip().splitlines()[-1] if attempt.error else None,
        "error": attempt.error,
        "tokens": attempt.usage.total_tokens,
        "ms": round(attempt.latency_ms),
    }
