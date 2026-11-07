import { useMutation, useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, FileCode, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { api, type Info, type Output, type Privacy, type Run, type Settings } from "../lib/api";
import { Button, cx, Field, Input, Segmented, Select } from "./ui";

const privacyOptions: { value: Privacy; label: string; title: string }[] = [
  { value: "masked", label: "Fake rows", title: "Fake rows that keep the shape of your values" },
  { value: "none", label: "No values", title: "Column names and counts only" },
  { value: "raw", label: "Real rows", title: "Your first 3 rows as they are" },
];

export function TaskPanel({
  file, info, settings, privacy, onPrivacy, preset, onPreset, onOutput, onRunning,
}: {
  file: string;
  info: Info;
  settings: Settings;
  privacy: Privacy;
  onPrivacy: (p: Privacy) => void;
  preset: string;
  onPreset: (p: string) => void;
  onOutput: (o: Output) => void;
  onRunning: (running: boolean) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [provider, setProvider] = useState(settings.default);
  const chosen = settings.providers.find((p) => p.name === provider) ?? settings.providers[0];
  const [model, setModel] = useState(chosen.model);
  const [attempts, setAttempts] = useState("3");
  const [runId, setRunId] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => setModel(chosen.model), [chosen.model]);
  useEffect(() => setRunId(null), [file]);

  const ollama = useQuery({ queryKey: ["ollama"], queryFn: api.ollama, enabled: provider === "ollama" });
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId!),
    enabled: runId !== null,
    refetchInterval: (q) => (q.state.data?.status === "running" || !q.state.data ? 400 : false),
  });
  const status = run.data?.status;
  const running = runId !== null && (status === undefined || status === "running");

  useEffect(() => onRunning(running), [running, onRunning]);
  useEffect(() => {
    if (status === "done" && run.data?.output) onOutput(run.data.output);
    if (status === "failed" && run.data?.error) toast.error(run.data.error);
  }, [status, run.data, onOutput]);

  const start = useMutation({
    mutationFn: () =>
      api.startRun({
        file, instruction, provider, model, privacy,
        preset: preset || null,
        attempts: Number(attempts),
      }),
    onSuccess: ({ id }) => setRunId(id),
    onError: (e) => toast.error(e.message),
  });

  const apply = useMutation({
    mutationFn: async (code?: string) => api.apply({ file, code, preset: preset || null }),
    onSuccess: (output) => {
      if (!output) return;
      onOutput(output);
      toast.success("Ran the saved transform. No tokens used.");
    },
    onError: (e) => toast.error(e.message),
  });

  const runSaved = () => (info.desktop ? apply.mutate(undefined) : picker.current?.click());
  const missingKey = chosen.needs_key && !chosen.key;

  return (
    <aside className="flex min-h-0 w-[360px] shrink-0 flex-col border-r border-line bg-surface">
      <form
        className="grid gap-4 overflow-x-hidden overflow-y-auto p-5"
        onSubmit={(e) => {
          e.preventDefault();
          start.mutate();
        }}
      >
        <Field label="What should change?">
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={5}
            required
            placeholder="Title-case names, lowercase emails, parse dates and prices, drop duplicates"
            className="w-full resize-y rounded-md border border-line bg-raised px-2.5 py-2 text-[13px] leading-relaxed text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
          />
        </Field>

        <div className="grid grid-cols-[120px_1fr] gap-2">
          <Field label="Provider">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {settings.providers.map((p) => (
                <option key={p.name} value={p.name}>{label(p.name)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Model">
            <Input value={model} onChange={(e) => setModel(e.target.value)} list="local-models" spellCheck={false} />
          </Field>
          <datalist id="local-models">
            {ollama.data?.models.map((m) => <option key={m} value={m} />)}
          </datalist>
        </div>
        {missingKey && (
          <p className="-mt-2 text-xs text-warn">Add a {label(provider)} key in Settings first.</p>
        )}
        {provider === "ollama" && ollama.data && !ollama.data.running && (
          <p className="-mt-2 text-xs text-warn">Ollama isn't running on this computer.</p>
        )}

        <Field label="What the model may see">
          <Segmented label="What the model may see" value={privacy} onChange={onPrivacy} options={privacyOptions} />
        </Field>

        <Field label="Save as">
          <Segmented
            label="Save as"
            value={preset || "plain"}
            onChange={(v) => onPreset(v === "plain" ? "" : v)}
            options={[
              { value: "plain", label: "Same format", title: "Keep the file type you opened" },
              ...info.presets.map((p) => ({ value: p.name, label: label(p.name), title: `${label(p.name)} (${p.suffix})` })),
            ]}
          />
        </Field>

        <Field label="Tries" hint="If the code crashes or loses values, the error goes back to the model.">
          <div className="w-24">
            <Select value={attempts} onChange={(e) => setAttempts(e.target.value)}>
              {[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}
            </Select>
          </div>
        </Field>

        <Button type="submit" tone="accent" className="h-9" disabled={running || start.isPending || missingKey}>
          {running ? <><LoaderCircle size={15} className="animate-spin" /> Cleaning…</> : "Clean the file"}
        </Button>

        {run.data && <Attempts run={run.data} running={running} />}
      </form>

      <div className="mt-auto border-t border-line p-5">
        <p className="text-[13px] text-ink-2">Already have a transform? Run it again on this file without the model.</p>
        <Button className="mt-2.5" onClick={runSaved} disabled={apply.isPending || running}>
          <FileCode size={15} /> Run a saved transform
        </Button>
        <input
          ref={picker}
          type="file"
          accept=".py"
          hidden
          aria-label="Saved transform"
          onChange={async (e) => {
            const picked = e.target.files?.[0];
            e.target.value = "";
            if (picked) apply.mutate(await picked.text());
          }}
        />
      </div>
    </aside>
  );
}

function Attempts({ run, running }: { run: Run; running: boolean }) {
  return (
    <div className="grid gap-2" aria-live="polite">
      <ol className="grid gap-1.5">
        {run.attempts.map((a) => (
          <li key={a.number} className="attempt-in flex gap-2 text-[13px]">
            {a.ok ? (
              <CircleCheck size={15} className="mt-0.5 shrink-0 text-good" />
            ) : (
              <CircleAlert size={15} className="mt-0.5 shrink-0 text-warn" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex justify-between gap-2">
                <span className="font-medium">Try {a.number} {a.ok ? "worked" : "failed"}</span>
                <span className="cells shrink-0 text-ink-3">{a.tokens.toLocaleString()} tok, {(a.ms / 1000).toFixed(1)} s</span>
              </div>
              {a.summary && <p className="cells mt-0.5 line-clamp-3 text-ink-2 [overflow-wrap:anywhere]" title={a.error ?? undefined}>{a.summary}</p>}
            </div>
          </li>
        ))}
        {running && (
          <li className="flex items-center gap-2 text-[13px] text-ink-3">
            <LoaderCircle size={15} className="animate-spin" /> Waiting for try {run.attempts.length + 1}…
          </li>
        )}
      </ol>
      {!running && (
        <p className={cx("text-[13px]", run.status === "failed" ? "text-bad" : "text-ink-2")}>
          {run.status === "failed" ? run.error : `${keptNote(run)}Used ${run.usage.total.toLocaleString()} tokens in total.`}
        </p>
      )}
    </div>
  );
}

function keptNote(run: Run): string {
  const kept = run.attempts.find((a) => a.number === run.kept);
  return kept && !kept.ok ? `No try kept every value, so try ${kept.number} is shown: it lost the fewest. ` : "";
}

export function label(name: string): string {
  return ({ ollama: "Ollama", openai: "OpenAI", groq: "Groq", shopify: "Shopify", sqlite: "SQLite" } as Record<string, string>)[name] ?? name;
}
