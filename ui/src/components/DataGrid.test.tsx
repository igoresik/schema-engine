// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { cellText, DataGrid } from "./DataGrid";
import { notes } from "./ModelView";

afterEach(cleanup);

test("blank cells, missing headers and long tables are spelled out", () => {
  render(
    <DataGrid
      table={{ columns: ["Name", " "], rows: [["  ana  ", null], [true, 3]], total: 250 }}
      flagged={[1]}
    />,
  );
  expect(screen.getByText("no header")).toBeTruthy();
  expect(screen.getByText("blank")).toBeTruthy();
  expect(screen.getByText("True")).toBeTruthy();
  expect(screen.getByText("Showing the first 2 of 250 rows.")).toBeTruthy();
  expect(screen.getByText("no header").closest("th")?.className).toContain("text-warn");
});

test("cell text", () => {
  expect(cellText(null)).toBe("");
  expect(cellText(false)).toBe("False");
  expect(cellText(1.5)).toBe("1.5");
});

test("profile notes in plain words", () => {
  const column = {
    name: " Order Date",
    dtype: "str",
    semantic_type: "date",
    null_count: 1,
    null_pct: 2,
    unique_count: 40,
    patterns: {},
    placeholder_nulls: 6,
    untrimmed_values: 1,
    header_issues: ["whitespace", "duplicate"],
  };
  expect(notes({ ...column, date_order: "inconsistent" })).toEqual([
    "spaces around header",
    "header used twice",
    "6 placeholder blanks",
    "1 with extra spaces",
    "day and month order mixed",
  ]);
  expect(notes({ ...column, header_issues: [], placeholder_nulls: 0, untrimmed_values: 0, date_order: "day_first" })).toEqual([
    "dates day first",
  ]);
});
