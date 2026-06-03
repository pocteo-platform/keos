/**
 * Phase 14.11 — pagination invariants.
 *
 * Both IncidentTable (PAGE=50) and AuditTable (PAGE=50) and Optimization
 * (PAGE=25) and Cluster Events (PAGE=25) share the same prev/next + n/m
 * indicator math. Rather than render-test each component (heavy, brittle
 * to UI changes), we extract the pure pagination math as a tested helper
 * and prove the invariants.
 *
 * This proves the pagination math itself is correct; per-component
 * rendering is exercised by the live E2E suite.
 */

import { describe, expect, it } from "vitest";

interface PageState {
  total: number;
  offset: number;
  pageSize: number;
  pageNum: number;
  pageMax: number;
  showingFrom: number;
  showingTo: number;
}

/** Pure pagination math — mirrors the shape used in IncidentTable.tsx,
 *  AuditTable.tsx, optimization.tsx, EventsList.tsx. */
function paginationState(total: number, offset: number, pageSize: number): PageState {
  const pageNum = Math.floor(offset / pageSize);
  const pageMax = Math.max(0, Math.ceil(total / pageSize) - 1);
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + pageSize, total);
  return { total, offset, pageSize, pageNum, pageMax, showingFrom, showingTo };
}

describe("pagination math", () => {
  it("renders 1/1 with empty data", () => {
    const s = paginationState(0, 0, 50);
    expect(s.pageNum).toBe(0);
    expect(s.pageMax).toBe(0);
    expect(s.showingFrom).toBe(0);
    expect(s.showingTo).toBe(0);
  });

  it("exact-page total renders the right pageMax", () => {
    const s = paginationState(50, 0, 50);
    expect(s.pageMax).toBe(0); // exactly one page
    expect(s.showingFrom).toBe(1);
    expect(s.showingTo).toBe(50);
  });

  it("one-over-page renders 2 pages", () => {
    const s = paginationState(51, 0, 50);
    expect(s.pageMax).toBe(1);
    expect(s.showingFrom).toBe(1);
    expect(s.showingTo).toBe(50);
  });

  it("last page shows partial range", () => {
    const s = paginationState(73, 50, 25);
    expect(s.pageNum).toBe(2);
    expect(s.pageMax).toBe(2);
    expect(s.showingFrom).toBe(51);
    expect(s.showingTo).toBe(73);
  });

  it("first page of 200 items, page 50", () => {
    const s = paginationState(200, 0, 50);
    expect(s.pageMax).toBe(3); // pages 1..4
    expect(s.showingFrom).toBe(1);
    expect(s.showingTo).toBe(50);
  });

  it("page 2 of 200 items, page 50", () => {
    const s = paginationState(200, 50, 50);
    expect(s.pageNum).toBe(1);
    expect(s.showingFrom).toBe(51);
    expect(s.showingTo).toBe(100);
  });

  it("optimization table at 25/page", () => {
    const s = paginationState(73, 50, 25);
    expect(s.pageNum).toBe(2);
    expect(s.pageMax).toBe(2);
  });

  it("cluster events at 25/page with 200 fetched", () => {
    const s = paginationState(200, 0, 25);
    expect(s.pageMax).toBe(7); // 8 pages: 25*8 = 200
  });

  it("offset = total - 1 still shows a single row", () => {
    const s = paginationState(100, 99, 50);
    expect(s.pageNum).toBe(1);
    expect(s.showingFrom).toBe(100);
    expect(s.showingTo).toBe(100);
  });

  it("offset past total still renders sanely (defensive)", () => {
    // Shouldn't happen in practice — but the table shouldn't crash if
    // a search reduces results below the current offset.
    const s = paginationState(10, 50, 50);
    expect(s.showingFrom).toBe(51);
    expect(s.showingTo).toBe(10); // clamps to total
  });

  it("prev button should be disabled at page 0", () => {
    const s = paginationState(200, 0, 50);
    expect(s.pageNum <= 0).toBe(true);
  });

  it("next button should be disabled at the last page", () => {
    const s = paginationState(200, 150, 50);
    expect(s.pageNum >= s.pageMax).toBe(true);
  });
});

describe("pagination edge cases — small totals", () => {
  it("1 item", () => {
    const s = paginationState(1, 0, 50);
    expect(s.pageMax).toBe(0);
    expect(s.showingFrom).toBe(1);
    expect(s.showingTo).toBe(1);
  });

  it("page size larger than total", () => {
    const s = paginationState(7, 0, 50);
    expect(s.pageMax).toBe(0);
    expect(s.showingFrom).toBe(1);
    expect(s.showingTo).toBe(7);
  });
});

describe("pagination indicator string", () => {
  // Mirrors the "n/m" badge shown in the table footers.
  function indicator(s: PageState): string {
    return `${s.pageNum + 1}/${s.pageMax + 1}`;
  }

  it("shows 1/1 on a single-page result", () => {
    expect(indicator(paginationState(10, 0, 50))).toBe("1/1");
  });

  it("shows 1/4 on the first of 4 pages", () => {
    expect(indicator(paginationState(200, 0, 50))).toBe("1/4");
  });

  it("shows 4/4 on the last of 4 pages", () => {
    expect(indicator(paginationState(200, 150, 50))).toBe("4/4");
  });
});
