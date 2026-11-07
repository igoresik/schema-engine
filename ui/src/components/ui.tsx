import { X } from "lucide-react";
import { Dialog as D, ToggleGroup } from "radix-ui";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

type Tone = "accent" | "plain" | "quiet";

const tones: Record<Tone, string> = {
  accent: "bg-accent text-white hover:brightness-110 dark:text-[#0c1a17]",
  plain: "bg-raised text-ink border border-line hover:border-ink-3",
  quiet: "text-ink-2 hover:text-ink hover:bg-raised",
};

export function Button({ tone = "plain", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone }) {
  return (
    <button
      type="button"
      className={cx(
        "press inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium whitespace-nowrap disabled:opacity-45",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function Segmented<V extends string>({
  value, onChange, options, label,
}: {
  value: V;
  onChange: (v: V) => void;
  options: { value: V; label: ReactNode; title?: string }[];
  label: string;
}) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as V)}
      aria-label={label}
      className="grid auto-cols-fr grid-flow-col rounded-md bg-bg p-0.5"
    >
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          title={o.title}
          className="h-7 rounded-[5px] px-2 text-[13px] font-medium text-ink-2 hover:text-ink data-[state=on]:bg-raised data-[state=on]:text-ink data-[state=on]:shadow-sm"
        >
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

const field =
  "h-8 w-full rounded-md border border-line bg-raised px-2.5 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(field, className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx(field, "pr-7", className)} {...props} />;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[13px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-3">{hint}</span>}
    </label>
  );
}

export function Dialog({
  open, onOpenChange, title, description, children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 bg-black/30 dark:bg-black/55" />
        <D.Content className="fixed top-1/2 left-1/2 max-h-[85vh] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-line bg-surface p-5 shadow-xl focus:outline-none">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <D.Title className="text-base font-semibold">{title}</D.Title>
              {description && <D.Description className="mt-0.5 text-[13px] text-ink-2">{description}</D.Description>}
            </div>
            <D.Close className="press grid size-7 place-items-center rounded-md text-ink-2 hover:bg-raised hover:text-ink" aria-label="Close">
              <X size={16} />
            </D.Close>
          </div>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
