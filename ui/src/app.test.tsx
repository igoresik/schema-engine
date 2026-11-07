// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "./app";

const settings = {
  default: "groq",
  providers: [
    { name: "ollama", needs_key: false, key_env: null, default_model: "llama3.1", model: "llama3.1", default_base_url: "u", base_url: "u", key: null, key_in_app: false },
    { name: "groq", needs_key: true, key_env: "GROQ_API_KEY", default_model: "openai/gpt-oss-120b", model: "openai/gpt-oss-120b", default_base_url: "g", base_url: "g", key: "gsk…7890", key_in_app: true },
  ],
};
const file = {
  id: "f1",
  name: "dirty_orders.csv",
  path: "C:/data/dirty_orders.csv",
  sheet: 0,
  sheets: [],
  table: { columns: ["customer name", "Order Date"], rows: [["farah petrov", "21.07.24"]], total: 52 },
};
const output = {
  id: "o1",
  table: { columns: ["customer_name", "order_date"], rows: [["Farah Petrov", null]], total: 50 },
  code: "def transform(df):\n    return df\n",
  lost: [{ column: "Order Date", blanked: 5, filled: 45 }],
  preset: null,
  suggested: { data: "dirty_orders_clean.csv", transform: "dirty_orders_transform.py" },
};

let calls: { url: string; body: unknown }[] = [];
let polls = 0;

beforeEach(() => {
  calls = [];
  polls = 0;
  const query = { matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} };
  vi.stubGlobal("matchMedia", () => query); // sonner follows the system theme
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ url, body });
    const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url === "/api/info") return reply({ version: "0.3.0", desktop: false, presets: [{ name: "shopify", suffix: ".csv" }, { name: "sqlite", suffix: ".sql" }], privacy_modes: ["masked", "none", "raw"] });
    if (url === "/api/settings") return reply(settings);
    if (url === "/api/files/sample") return reply(file);
    if (url === "/api/runs") return reply({ id: "r1" });
    if (url === "/api/runs/r1") {
      polls += 1;
      const failed = { number: 1, ok: false, summary: "AttributeError: no .str", error: "AttributeError: no .str", tokens: 3510, ms: 4352 };
      return reply(
        polls < 2
          ? { id: "r1", status: "running", error: null, attempts: [failed], usage: { prompt: 0, completion: 0, total: 3510 }, output: null }
          : { id: "r1", status: "done", error: null, attempts: [failed, { ...failed, number: 2, ok: true, summary: null, error: null }], usage: { prompt: 4732, completion: 2893, total: 7625 }, output },
      );
    }
    if (url.startsWith("/api/files/f1/profile")) return reply({ profile: { source: "x", row_count: 52, column_count: 2, duplicate_rows: 0, privacy_mode: "masked", columns: [] }, tokens: 961 });
    if (url === "/api/outputs/o1/save") return reply({ path: "C:/data/dirty_orders_clean.csv" });
    return new Response(JSON.stringify({ detail: `unexpected ${url}` }), { status: 500 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

test("sample file, a run with a failed try, then save", async () => {
  const user = userEvent.setup();
  renderApp();
  await user.click(await screen.findByRole("button", { name: "Try the sample file" }));
  expect(await screen.findByText("farah petrov")).toBeTruthy();
  expect(screen.getByText("52 rows")).toBeTruthy();
  expect((screen.getByRole("combobox", { name: "Provider" }) as HTMLSelectElement).value).toBe("groq");

  await user.type(screen.getByRole("textbox", { name: "What should change?" }), "Title-case names");
  await user.click(screen.getByRole("button", { name: "Clean the file" }));
  expect(await screen.findByText("About 961 tokens, whatever the size of the file")).toBeTruthy();
  expect(await screen.findByText("Try 1 failed")).toBeTruthy();
  expect(await screen.findByText("Used 7,625 tokens in total.", {}, { timeout: 3000 })).toBeTruthy();
  expect(screen.getByText("Farah Petrov")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("Order Date lost 5 of 45");

  const run = calls.find((c) => c.url === "/api/runs")!.body;
  expect(run).toEqual({ file: "f1", instruction: "Title-case names", provider: "groq", model: "openai/gpt-oss-120b", privacy: "masked", preset: null, attempts: 3 });

  await user.click(screen.getByRole("button", { name: "Save cleaned file" }));
  expect(calls.at(-1)).toEqual({ url: "/api/outputs/o1/save", body: { kind: "data" } });
});

test("a cloud provider without a key can't start", async () => {
  settings.providers[1].key = null;
  const user = userEvent.setup();
  renderApp();
  await user.click(await screen.findByRole("button", { name: "Try the sample file" }));
  expect(await screen.findByText("Add a Groq key in Settings first.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Clean the file" }) as HTMLButtonElement).disabled).toBe(true);
  settings.providers[1].key = "gsk…7890";
});
