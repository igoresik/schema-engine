import type { ReactNode } from "react";

import type { Cell, Table } from "../lib/api";
import { cx } from "./ui";

export function cellText(value: Cell): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

/** The first rows of a table. Whitespace stays visible, since stray spaces are part of the mess. */
export function DataGrid({
  table, flagged = [], header, masked = false,
}: {
  table: Table;
  /** columns that lost values, by position */
  flagged?: number[];
  header?: (column: string, index: number) => ReactNode;
  masked?: boolean;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="cells w-max min-w-full border-separate border-spacing-0">
        <thead className="sticky top-0 z-10">
          <tr>
            <th className="sticky left-0 z-10 border-r border-b border-line bg-surface px-2.5 py-2 text-right font-normal text-ink-3">#</th>
            {table.columns.map((name, i) => (
              <th
                key={i}
                scope="col"
                className={cx(
                  "border-b border-line bg-surface px-3 py-2 text-left align-bottom font-sans text-[13px] font-semibold",
                  flagged.includes(i) && "bg-warn-soft text-warn",
                )}
              >
                {name.trim() ? <span className="whitespace-pre">{name}</span> : <span className="font-normal text-ink-3 italic">no header</span>}
                {header?.(name, i)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, r) => (
            <tr key={r} className="hover:bg-raised">
              <td className="sticky left-0 border-r border-b border-line bg-surface px-2.5 py-1.5 text-right text-ink-3">{r + 1}</td>
              {row.map((value, c) => (
                <td key={c} className={cx("max-w-[320px] border-b border-line px-3 py-1.5", typeof value === "number" && "text-right")}>
                  {value === null ? (
                    <span className="text-ink-3">blank</span>
                  ) : (
                    <span className={cx("block truncate whitespace-pre", masked && "masked inline")}>{cellText(value)}</span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.total > table.rows.length && (
        <p className="px-3 py-3 text-[13px] text-ink-3">
          Showing the first {table.rows.length} of {table.total.toLocaleString()} rows.
        </p>
      )}
    </div>
  );
}
