import { useMutation, useQuery } from "@tanstack/react-query";
import { FileSpreadsheet, FolderOpen, Settings as Gear, TriangleAlert } from "lucide-react";
import { Tabs } from "radix-ui";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { toast, Toaster } from "sonner";

import { DataGrid } from "./components/DataGrid";
import { ModelView } from "./components/ModelView";
import { SettingsDialog } from "./components/SettingsDialog";
import { TaskPanel } from "./components/TaskPanel";
import { Button, Select } from "./components/ui";
import { api, type Info, type OpenFile, type Output, type Privacy, type Settings } from "./lib/api";

type View = "file" | "model" | "cleaned" | "code";

export function App() {
  const info = useQuery({ queryKey: ["info"], queryFn: api.info });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [file, setFile] = useState<OpenFile | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const opened = (next: OpenFile | null) => next && setFile(next);
  const fail = (e: Error) => toast.error(e.message);
  const open = useMutation({ mutationFn: (args?: { path: string; sheet: string }) => api.open(args?.path, args?.sheet), onSuccess: opened, onError: fail });
  const drop = useMutation({ mutationFn: api.drop, onSuccess: opened, onError: fail });
  const sample = useMutation({ mutationFn: api.sample, onSuccess: opened, onError: fail });

  const chooseFile = () => (info.data?.desktop ? open.mutate(undefined) : picker.current?.click());
  const [dragging, setDragging] = useState(false);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) drop.mutate(dropped);
  };

  if (!info.data || !settings.data) {
    return <div className="grid h-full place-items-center text-ink-3">{info.error?.message ?? settings.error?.message ?? "Starting…"}</div>;
  }

  return (
    <div
      className="flex h-full flex-col"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={onDrop}
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
        <img src="/icon.svg" alt="" className="size-5" />
        <span className="font-semibold">Schema Engine</span>
        {file && (
          <div className="ml-3 flex min-w-0 items-center gap-2 border-l border-line pl-4 text-[13px]">
            <FileSpreadsheet size={15} className="shrink-0 text-ink-3" />
            <span className="truncate font-medium" title={file.path ?? file.name}>{file.name}</span>
            {file.sheets.length > 1 && (
              <Select
                aria-label="Sheet"
                className="h-7 w-auto"
                value={String(file.sheet === 0 ? file.sheets[0] : file.sheet)}
                disabled={!file.path}
                title={file.path ? undefined : "Open the file with Open another to switch sheets"}
                onChange={(e) => file.path && open.mutate({ path: file.path, sheet: e.target.value })}
              >
                {file.sheets.map((s) => <option key={s}>{s}</option>)}
              </Select>
            )}
            <span className="text-ink-3">{file.table.total.toLocaleString()} rows</span>
          </div>
        )}
        <div className="ml-auto flex items-center gap-1">
          {file && (
            <Button tone="quiet" onClick={chooseFile}>
              <FolderOpen size={15} /> Open another
            </Button>
          )}
          <Button tone="quiet" onClick={() => setSettingsOpen(true)}>
            <Gear size={15} /> Settings
          </Button>
        </div>
      </header>

      {file ? (
        <Workspace key={file.id} file={file} info={info.data} settings={settings.data} />
      ) : (
        <Start
          version={info.data.version}
          onOpen={chooseFile}
          onSample={() => sample.mutate()}
          busy={open.isPending || drop.isPending || sample.isPending}
        />
      )}

      <input
        ref={picker}
        type="file"
        accept=".csv,.tsv,.txt,.xlsx,.xlsm"
        hidden
        aria-label="Spreadsheet"
        onChange={(e) => {
          const picked = e.target.files?.[0];
          e.target.value = "";
          if (picked) drop.mutate(picked);
        }}
      />
      {dragging && (
        <div className="pointer-events-none fixed inset-3 z-50 grid place-items-center rounded-xl border-2 border-dashed border-accent bg-accent-soft/80 text-[15px] font-medium text-accent">
          Drop to open
        </div>
      )}
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} settings={settings.data} />
      <Toaster position="bottom-right" theme="system" richColors closeButton />
    </div>
  );
}

function Start({ version, onOpen, onSample, busy }: { version: string; onOpen: () => void; onSample: () => void; busy: boolean }) {
  return (
    <main className="grid flex-1 place-items-center overflow-auto p-6">
      <div className="w-full max-w-[560px]">
        <h1 className="text-[34px] leading-[1.1] font-semibold tracking-[-0.02em]">Clean a messy spreadsheet. Your rows stay on this computer.</h1>
        <p className="mt-3 max-w-[60ch] text-[15px] text-ink-2">
          Say what to fix in plain words. The model gets a short profile of your columns, never your rows, and writes
          Python that runs here on your computer. Keep the code and next month's file cleans itself.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <Button tone="accent" className="h-10 px-4 text-sm" onClick={onOpen} disabled={busy}>
            <FolderOpen size={16} /> Open a file
          </Button>
          <Button className="h-10 px-4 text-sm" onClick={onSample} disabled={busy}>
            Try the sample file
          </Button>
        </div>
        <p className="mt-3 text-[13px] text-ink-3">CSV, TSV or Excel. You can also drop a file anywhere in this window.</p>
        <p className="mt-12 text-xs text-ink-3">Version {version}</p>
      </div>
    </main>
  );
}

function Workspace({ file, info, settings }: { file: OpenFile; info: Info; settings: Settings }) {
  const [view, setView] = useState<View>("file");
  const [privacy, setPrivacy] = useState<Privacy>("masked");
  const [preset, setPreset] = useState("");
  const [output, setOutput] = useState<Output | null>(null);
  const [running, setRunning] = useState(false);

  const showOutput = useCallback((next: Output) => {
    setOutput(next);
    setView("cleaned");
  }, []);
  useEffect(() => {
    if (running) setView("model");
  }, [running]);

  return (
    <div className="flex min-h-0 flex-1">
      <TaskPanel
        file={file.id}
        info={info}
        settings={settings}
        privacy={privacy}
        onPrivacy={setPrivacy}
        preset={preset}
        onPreset={setPreset}
        onOutput={showOutput}
        onRunning={setRunning}
      />
      <Tabs.Root value={view} onValueChange={(v) => setView(v as View)} className="flex min-w-0 flex-1 flex-col">
        <Tabs.List className="flex h-11 shrink-0 items-end gap-5 border-b border-line px-5" aria-label="Views">
          {([
            ["file", "Your file"],
            ["model", "What the model sees"],
            ["cleaned", "Cleaned"],
            ["code", "Transform"],
          ] as const).map(([value, text]) => (
            <Tabs.Trigger
              key={value}
              value={value}
              disabled={(value === "cleaned" || value === "code") && !output}
              className="-mb-px border-b-2 border-transparent pb-2.5 text-[13px] font-medium text-ink-2 hover:text-ink disabled:text-ink-3 disabled:opacity-60 data-[state=active]:border-accent data-[state=active]:text-ink"
            >
              {text}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="file" className="flex min-h-0 flex-1 flex-col">
          <DataGrid table={file.table} />
        </Tabs.Content>
        <Tabs.Content value="model" className="flex min-h-0 flex-1 flex-col">
          <ModelView file={file.id} privacy={privacy} />
        </Tabs.Content>
        {output && (
          <>
            <Tabs.Content value="cleaned" className="flex min-h-0 flex-1 flex-col">
              <SaveBar output={output} />
              <DataGrid table={output.table} flagged={lostPositions(output)} />
            </Tabs.Content>
            <Tabs.Content value="code" className="flex min-h-0 flex-1 flex-col">
              <SaveBar output={output} />
              <pre className="cells min-h-0 flex-1 overflow-auto p-5 leading-relaxed text-ink">{output.code}</pre>
            </Tabs.Content>
          </>
        )}
      </Tabs.Root>
    </div>
  );
}

function lostPositions(output: Output): number[] {
  const key = (name: string) => name.trim().toLowerCase().replace(/[^0-9a-z]+/g, "_").replace(/^_|_$/g, "");
  const lost = new Set(output.lost.map((l) => key(l.column)));
  return output.table.columns.flatMap((c, i) => (lost.has(key(c)) ? [i] : []));
}

function SaveBar({ output }: { output: Output }) {
  const save = useMutation({
    mutationFn: (kind: "data" | "transform") => api.save(output.id, kind),
    onSuccess: (saved, kind) => saved && toast.success(`Saved the ${kind === "data" ? "cleaned file" : "transform"} to ${saved.path}`),
    onError: (e) => toast.error(e.message),
  });
  return (
    <div className="shrink-0 border-b border-line">
      {output.lost.length > 0 && (
        <div className="flex gap-2.5 bg-warn-soft px-5 py-2.5 text-[13px] text-warn" role="status">
          <TriangleAlert size={16} className="mt-0.5 shrink-0" />
          <p>
            Some values became blank:{" "}
            {output.lost.map((l) => `${l.column} lost ${l.blanked} of ${l.filled}`).join(", ")}. Check those columns before
            you use the file.
          </p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 px-5 py-2.5">
        <Button tone="accent" onClick={() => save.mutate("data")} disabled={save.isPending}>
          Save cleaned file
        </Button>
        <Button onClick={() => save.mutate("transform")} disabled={save.isPending}>
          Save transform
        </Button>
        <span className="text-[13px] text-ink-3">
          {output.table.total.toLocaleString()} rows. Suggested name: {output.suggested.data}
        </span>
      </div>
    </div>
  );
}
