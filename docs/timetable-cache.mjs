import { validateTimetable } from "./timetable-utils.mjs";

const CACHE_KEY = "timetable-snapshot-v1";

export function readSavedTimetable(storage) {
  try {
    const data = validateTimetable(JSON.parse((storage ?? globalThis.localStorage).getItem(CACHE_KEY)));
    if (data.group.id !== "653" || !Number.isFinite(Date.parse(data.published_at))) return null;
    return data;
  } catch {
    return null;
  }
}

export function saveTimetable(data, storage) {
  try {
    (storage ?? globalThis.localStorage).setItem(CACHE_KEY, JSON.stringify(data));
  } catch {
    // Storage can be unavailable or full. The current timetable still works.
  }
}

export function newestTimetable(saved, bundled) {
  if (!saved) return bundled;
  if (!bundled) return saved;
  const savedUpdated = Date.parse(saved.source_updated_at) || 0;
  const bundledUpdated = Date.parse(bundled.source_updated_at) || 0;
  if (savedUpdated !== bundledUpdated) return savedUpdated > bundledUpdated ? saved : bundled;
  return Date.parse(saved.published_at) > Date.parse(bundled.published_at) ? saved : bundled;
}

export function timetableChanged(before, after) {
  if (!before) return true;
  return JSON.stringify([before.source_updated_at, before.group, before.weeks])
    !== JSON.stringify([after.source_updated_at, after.group, after.weeks]);
}
