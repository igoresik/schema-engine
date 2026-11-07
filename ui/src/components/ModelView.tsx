import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ColumnProfile, type Privacy } from "../lib/api";
import { DataGrid } from "./DataGrid";
import { Button } from "./ui";

const modes: Record<Privacy, string> = {
  masked: "Fake rows that keep the shape of your values. Your real values stay on this computer.",
  none: "Column names and counts only. No values at all, not even fake ones.",
  raw: "Your first 3 rows as they are. Use this only for public data or a local model.",
};

const headerIssues: Record<string, string> = {
  duplicate: "header used twice",
  unnamed: "no header",
  whitespace: "spaces around header",
};

export function notes(column: ColumnProfile): string[] {
  const out = column.header_issues.map((issue) => headerIssues[issue] ?? issue);
  if (column.placeholder_nulls) out.push(`${column.placeholder_nulls} placeholder blanks`);
  if (column.untrimmed_values) out.push(`${column.untrimmed_values} with extra spaces`);
  if (column.date_order === "inconsistent") out.push("day and month order mixed");
  else if (column.date_order) out.push(`dates ${column.date_order.replace("_", " ")}`);
  return out;
}

export function ModelView({ file, privacy }: { file: string; privacy: Privacy }) {
  const [showJson, setShowJson] = useState(false);
  const { data, error } = useQuery({ queryKey: ["profile", file, privacy], queryFn: () => api.profile(file, privacy) });
  if (error) return <p className="p-6 text-bad">{error.message}</p>;
  if (!data) return <p className="p-6 text-ink-3">Reading the file…</p>;
  const { profile } = data;
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="max-w-[1100px] px-6 py-5">
        <h2 className="text-[22px] leading-tight font-semibold tracking-[-0.01em]">
          About {data.tokens.toLocaleString()} tokens, whatever the size of the file
        </h2>
        <p className="mt-1.5 max-w-[68ch] text-ink-2">
          This is everything the model gets for your {profile.row_count.toLocaleString()} rows. {modes[privacy]}
        </p>

        <table className="mt-5 w-full border-separate border-spacing-0 text-[13px]">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="border-b border-line py-2 pr-4 font-medium">Column</th>
              <th className="border-b border-line py-2 pr-4 font-medium">Kind</th>
              <th className="border-b border-line py-2 pr-4 text-right font-medium">Blank</th>
              {privacy !== "none" && <th className="border-b border-line py-2 pr-4 font-medium">Formats found</th>}
              <th className="border-b border-line py-2 font-medium">Worth knowing</th>
            </tr>
          </thead>
          <tbody>
            {profile.columns.map((column, i) => (
              <tr key={i} className="align-top">
                <td className="border-b border-line py-2 pr-4 font-medium whitespace-pre">
                  {column.name.trim() ? column.name : <span className="font-normal text-ink-3 italic">no header</span>}
                </td>
                <td className="border-b border-line py-2 pr-4 text-ink-2">{column.semantic_type}</td>
                <td className="cells border-b border-line py-2 pr-4 text-right text-ink-2">{column.null_pct}%</td>
                {privacy !== "none" && (
                  <td className="border-b border-line py-2 pr-4">
                    <div className="cells flex max-w-[420px] flex-wrap gap-x-2 gap-y-1">
                      {(column.value_shapes ?? []).map((shape) => (
                        <span key={shape} className="masked whitespace-pre">{shape}</span>
                      ))}
                    </div>
                  </td>
                )}
                <td className="border-b border-line py-2 text-ink-2">{notes(column).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {profile.sample && (
          <>
            <h3 className="mt-7 text-[15px] font-semibold">{privacy === "raw" ? "Your first rows" : "Example rows it gets"}</h3>
            <div className="mt-2 flex max-h-[220px] overflow-hidden rounded-md border border-line bg-surface">
              <DataGrid
                table={{ columns: profile.columns.map((c) => c.name), rows: profile.sample, total: profile.sample.length }}
                masked={privacy === "masked"}
              />
            </div>
          </>
        )}

        <Button tone="quiet" className="mt-5 -ml-3" onClick={() => setShowJson(!showJson)} aria-expanded={showJson}>
          {showJson ? "Hide the exact JSON" : "Show the exact JSON"}
        </Button>
        {showJson && (
          <pre className="cells mt-2 max-h-[420px] overflow-auto rounded-md border border-line bg-surface p-4 text-ink-2">
            {JSON.stringify(profile, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
