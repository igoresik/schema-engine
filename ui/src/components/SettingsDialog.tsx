import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { api, type Provider, type Settings } from "../lib/api";
import { label } from "./TaskPanel";
import { Button, Dialog, Field, Input, Segmented } from "./ui";

export function SettingsDialog({ open, onOpenChange, settings }: { open: boolean; onOpenChange: (o: boolean) => void; settings: Settings }) {
  const [tab, setTab] = useState(settings.default);
  const client = useQueryClient();
  const setDefault = useMutation({
    mutationFn: api.setDefault,
    onSuccess: (s) => client.setQueryData(["settings"], s),
  });
  const provider = settings.providers.find((p) => p.name === tab)!;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Settings"
      description="Keys are kept in Windows Credential Manager, never in a file. The CLI uses them too."
    >
      <Segmented label="Provider" value={tab} onChange={setTab} options={settings.providers.map((p) => ({ value: p.name, label: label(p.name) }))} />
      <ProviderForm key={provider.name} provider={provider} />
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4 text-[13px]">
        {settings.default === provider.name ? (
          <span className="text-ink-2">{label(provider.name)} is used when the app opens.</span>
        ) : (
          <>
            <span className="text-ink-2">The app opens with {label(settings.default)}.</span>
            <Button onClick={() => setDefault.mutate(provider.name)}>Open with {label(provider.name)}</Button>
          </>
        )}
      </div>
    </Dialog>
  );
}

function ProviderForm({ provider }: { provider: Provider }) {
  const client = useQueryClient();
  const [model, setModel] = useState(provider.model);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [key, setKey] = useState("");

  const save = useMutation({
    mutationFn: (body: { model?: string; base_url?: string; api_key?: string }) => api.saveProvider(provider.name, body),
    onSuccess: (saved) => {
      client.setQueryData<Settings>(["settings"], (old) =>
        old && { ...old, providers: old.providers.map((p) => (p.name === saved.name ? saved : p)) },
      );
      setKey("");
      toast.success(`Saved ${label(saved.name)} settings.`);
    },
    onError: (e) => toast.error(e.message),
  });
  const test = useMutation({
    mutationFn: () => api.test(provider.name),
    onSuccess: (r) =>
      r.ok
        ? toast.success(`${label(provider.name)} answered in ${Math.round(r.latency_ms ?? 0)} ms.`)
        : toast.error(r.detail),
    onError: (e) => toast.error(e.message),
  });

  return (
    <form
      className="mt-4 grid gap-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ model, base_url: baseUrl, ...(key ? { api_key: key } : {}) });
      }}
    >
      <Field label="Model" hint={model !== provider.default_model ? `Default: ${provider.default_model}` : undefined}>
        <Input value={model} onChange={(e) => setModel(e.target.value)} spellCheck={false} />
      </Field>
      <Field label="Address">
        <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} spellCheck={false} />
      </Field>
      {provider.needs_key && (
        <Field
          label="API key"
          hint={
            provider.key
              ? provider.key_in_app
                ? `Saved: ${provider.key}`
                : `Using ${provider.key} from ${provider.key_env}.`
              : `Paste a key from your ${label(provider.name)} account.`
          }
        >
          <div className="flex gap-2">
            <Input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={provider.key ? "Paste a new key to replace it" : "Paste your key"}
              autoComplete="off"
            />
            {provider.key_in_app && (
              <Button tone="quiet" onClick={() => save.mutate({ api_key: "" })}>Remove</Button>
            )}
          </div>
        </Field>
      )}
      <div className="flex gap-2">
        <Button type="submit" tone="accent" disabled={save.isPending}>Save</Button>
        <Button onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
      </div>
      <p className="text-xs text-ink-3">Test connection sends a one-token request.</p>
    </form>
  );
}
