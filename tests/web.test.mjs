import test from "node:test";
import assert from "node:assert/strict";

import {
  buildIcs,
  classMatchesFilter,
  dateInTimeZone,
  filterClasses,
  getLiveState,
  selectInitialWeek,
  validateTimetable,
} from "../docs/timetable-utils.mjs";

const shared = {
  start_time: "10:00",
  end_time: "11:25",
  starts_at: "2026-09-08T10:00:00+03:00",
  ends_at: "2026-09-08T11:25:00+03:00",
  subject: "Economic Theory, Part 1",
  teacher: "Teacher; Name",
  room: "Room 1",
  subgroup: null,
};

const subgroupOne = {
  ...shared,
  subject: "Foreign Language",
  subgroup: "1 подгр",
};

const subgroupTwo = {
  ...subgroupOne,
  teacher: "Another Teacher",
  subgroup: "2 подгр",
};

const timetable = {
  schema_version: 1,
  group: { id: "653", name: "Group 653" },
  weeks: [
    {
      starts_on: "2026-08-31",
      days: [{ date: "2026-09-01", classes: [] }],
    },
    {
      starts_on: "2026-09-07",
      days: [{ date: "2026-09-08", classes: [shared, subgroupOne, subgroupTwo] }],
    },
    {
      starts_on: "2026-09-14",
      days: [{ date: "2026-09-14", classes: [] }],
    },
  ],
};

test("Minsk calendar date is independent from the viewer timezone", () => {
  const instant = new Date("2026-09-05T21:30:00Z");
  assert.equal(dateInTimeZone(instant), "2026-09-06");
});

test("week selection discovers and chooses current, future, or latest weeks", () => {
  assert.equal(selectInitialWeek(timetable.weeks, "2026-09-08"), "2026-09-07");
  assert.equal(selectInitialWeek(timetable.weeks, "2026-09-13"), "2026-09-07");
  assert.equal(selectInitialWeek(timetable.weeks, "2026-08-01"), "2026-08-31");
  assert.equal(selectInitialWeek(timetable.weeks, "2026-10-01"), "2026-09-14");
});

test("subgroup filters retain shared classes", () => {
  assert.equal(filterClasses([shared, subgroupOne, subgroupTwo], "1").length, 2);
  assert.equal(filterClasses([shared, subgroupOne, subgroupTwo], "2").length, 2);
  assert.equal(filterClasses([shared, subgroupOne, subgroupTwo], "all").length, 3);
  assert.equal(classMatchesFilter(subgroupTwo, "1"), false);
});

test("live state respects exact class boundaries", () => {
  const before = getLiveState(timetable, "1", new Date("2026-09-08T06:59:00Z"));
  assert.equal(before.kind, "next");
  assert.equal(before.minutes, 1);

  const atStart = getLiveState(timetable, "1", new Date("2026-09-08T07:00:00Z"));
  assert.equal(atStart.kind, "current");
  assert.equal(atStart.minutes, 85);

  const atEnd = getLiveState(timetable, "1", new Date("2026-09-08T08:25:00Z"));
  assert.equal(atEnd.kind, "none");
});

test("ICS output uses UTC, escapes text, and distinguishes subgroups", () => {
  const created = new Date("2026-09-05T09:00:00Z");
  const first = buildIcs(subgroupOne, "653", created);
  const second = buildIcs(subgroupTwo, "653", created);

  assert.match(first, /DTSTART:20260908T070000Z/);
  assert.match(first, /DTEND:20260908T082500Z/);
  assert.match(buildIcs(shared, "653", created), /SUMMARY:Economic Theory\\, Part 1/);
  assert.match(buildIcs(shared, "653", created), /Teacher\\; Name/);
  assert.notEqual(first.match(/UID:(.+)/)[1], second.match(/UID:(.+)/)[1]);
});

test("schema validation rejects unsupported and malformed payloads", () => {
  assert.equal(validateTimetable(timetable), timetable);
  assert.throws(() => validateTimetable({ schema_version: 2, weeks: [] }), /Неподдерживаемая/);
  assert.throws(
    () => validateTimetable({ schema_version: 1, group: {}, weeks: [{ starts_on: "x" }] }),
    /Некорректная/,
  );
});
