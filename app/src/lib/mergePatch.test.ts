import { describe, expect, test } from "vitest";
import { applyMergePatch } from "./mergePatch";

describe("applyMergePatch", () => {
  test("merges objects, replaces arrays and scalars", () => {
    const a = { now: { title: "A", pos_ms: 1 }, room: [1, 2], who: "auto" };
    expect(applyMergePatch(a, { now: { pos_ms: 5 }, room: [3], who: "board" })).toEqual({
      now: { title: "A", pos_ms: 5 }, room: [3], who: "board",
    });
    expect(a.now.pos_ms).toBe(1); // input untouched
  });
  test("null means the value is null (keys are never removed)", () => {
    expect(applyMergePatch({ last_touched: { bank: "A", ch: 1 } }, { last_touched: null })).toEqual({ last_touched: null });
  });
  test("object replacing null and vice versa", () => {
    expect(applyMergePatch({ a: null }, { a: { x: 1 } })).toEqual({ a: { x: 1 } });
    expect(applyMergePatch({ a: { x: 1 } }, { a: null })).toEqual({ a: null });
  });
});
