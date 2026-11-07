// Typed calls to the local API (src/schema_engine/app/api.py).

export type Privacy = "masked" | "none" | "raw";
export type Cell = string | number | boolean | null;

export interface Table {
  columns: string[];
  rows: Cell[][];
  total: number;
}

export interface OpenFile {
  id: string;
  name: string;
  /** null for a dropped file: only a copy exists */
  path: string | null;
  sheet: string | number;
  sheets: string[];
  table: Table;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  semantic_type: string;
  null_count: number;
  null_pct: number;
  unique_count: number;
  patterns: Record<string, number>;
  placeholder_nulls: number;
  untrimmed_values: number;
  date_order: string | null;
  /** left out in "none" mode */
  value_shapes?: string[];
  header_issues: string[];
}

export interface Profile {
  profile: {
    source: string;
    row_count: number;
    column_count: number;
    duplicate_rows: number;
    privacy_mode: Privacy;
    columns: ColumnProfile[];
    sample?: Cell[][];
  } & Record<string, unknown>;
  tokens: number;
}

export interface Lost {
  column: string;
  blanked: number;
  filled: number;
}

export interface Output {
  id: string;
  table: Table;
  code: string;
  lost: Lost[];
  preset: string | null;
  suggested: { data: string; transform: string };
}

export interface Attempt {
  number: number;
  ok: boolean;
  summary: string | null;
  error: string | null;
  tokens: number;
  ms: number;
}

export interface Run {
  id: string;
  status: "running" | "done" | "failed";
  error: string | null;
  attempts: Attempt[];
  usage: { prompt: number; completion: number; total: number };
  output: Output | null;
}

export interface Info {
  version: string;
  desktop: boolean;
  presets: { name: string; suffix: string }[];
  privacy_modes: Privacy[];
}

export interface Provider {
  name: string;
  needs_key: boolean;
  key_env: string | null;
  default_model: string;
  model: string;
  default_base_url: string;
  base_url: string;
  /** short fingerprint, never the key */
  key: string | null;
  key_in_app: boolean;
}

export interface Settings {
  default: string;
  providers: Provider[];
}

export interface Health {
  ok: boolean;
  detail: string;
  latency_ms?: number | null;
  tokens?: number;
}

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status})`);
  }
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  info: () => call<Info>("/api/info"),
  open: (path?: string, sheet = "0") => call<OpenFile | null>("/api/files/open", json("POST", { path, sheet })),
  drop: (file: File) =>
    call<OpenFile>(`/api/files/drop?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "content-type": "application/octet-stream" },
      body: file,
    }),
  sample: () => call<OpenFile>("/api/files/sample", { method: "POST" }),
  profile: (file: string, privacy: Privacy) => call<Profile>(`/api/files/${file}/profile?privacy=${privacy}`),
  startRun: (body: {
    file: string;
    instruction: string;
    provider: string;
    model: string;
    privacy: Privacy;
    preset: string | null;
    attempts: number;
  }) => call<{ id: string }>("/api/runs", json("POST", body)),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  apply: (body: { file: string; code?: string; preset: string | null }) => call<Output | null>("/api/apply", json("POST", body)),
  save: (output: string, kind: "data" | "transform") =>
    call<{ path: string } | null>(`/api/outputs/${output}/save`, json("POST", { kind })),
  settings: () => call<Settings>("/api/settings"),
  setDefault: (provider: string) => call<Settings>("/api/settings", json("PUT", { provider })),
  saveProvider: (name: string, body: { model?: string; base_url?: string; api_key?: string }) =>
    call<Provider>(`/api/settings/${name}`, json("PUT", body)),
  test: (name: string) => call<Health>(`/api/settings/${name}/test`, { method: "POST" }),
  ollama: () => call<{ running: boolean; models: string[] }>("/api/ollama"),
};
