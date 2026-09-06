import test from "node:test";
import assert from "node:assert/strict";
import { readSavedTimetable, saveTimetable, newestTimetable, timetableChanged } from "../docs/timetable-cache.mjs";

const bundled = {
  schema_version: 1,
  group: { id: "653", name: "Group 653" },
  source_updated_at: "2026-09-05T12:00:00+03:00",
  published_at: "2026-09-05T12:05:00+03:00",
  weeks: [],
};
const saved = { ...bundled, source_updated_at: "2026-09-06T12:00:00+03:00", published_at: "2026-09-06T12:05:00+03:00" };

test("a successful refresh survives a reload and an older bundled snapshot", () => {
  const data = new Map();
  const storage = { getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value) };
  saveTimetable(saved, storage);
  assert.deepEqual(newestTimetable(readSavedTimetable(storage), bundled), saved);
  assert.deepEqual(newestTimetable(readSavedTimetable(storage), null), saved);
  assert.equal(newestTimetable(bundled, saved), saved);
});

test("damaged, unrelated, or blocked storage does not break the timetable", () => {
  for (const value of ["invalid", "null", JSON.stringify({ ...saved, group: { id: "other" } }), JSON.stringify({ ...saved, weeks: [{}] })]) {
    assert.equal(readSavedTimetable({ getItem: () => value }), null);
  }
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("full"); } };
  assert.equal(readSavedTimetable(blocked), null);
  assert.doesNotThrow(() => saveTimetable(saved, blocked));
  assert.equal(newestTimetable(null, bundled), bundled);
});

test("refresh labels compare the visitor's timetable even on a new server instance", () => {
  assert.equal(timetableChanged(saved, { ...saved, published_at: "2026-09-07T12:00:00+03:00" }), false);
  assert.equal(timetableChanged(bundled, saved), true);
  assert.equal(timetableChanged(null, saved), true);
});
