const DAY_MS = 24 * 60 * 60 * 1000;

export function validateTimetable(data) {
  if (!data || typeof data !== "object") {
    throw new Error("Данные расписания отсутствуют.");
  }
  if (data.schema_version !== 1) {
    throw new Error(`Неподдерживаемая версия расписания: ${data.schema_version ?? "нет"}.`);
  }
  if (!data.group || !Array.isArray(data.weeks)) {
    throw new Error("Данные расписания неполные.");
  }
  for (const week of data.weeks) {
    if (typeof week.starts_on !== "string" || !Array.isArray(week.days)) {
      throw new Error("Некорректная неделя расписания.");
    }
    for (const day of week.days) {
      if (typeof day.date !== "string" || !Array.isArray(day.classes)) {
        throw new Error("Некорректный день расписания.");
      }
      for (const event of day.classes) {
        if (
          typeof event.starts_at !== "string" ||
          typeof event.ends_at !== "string" ||
          typeof event.subject !== "string"
        ) {
          throw new Error("Некорректное занятие в расписании.");
        }
      }
    }
  }
  return data;
}

export function dateInTimeZone(now = new Date(), timeZone = "Europe/Minsk") {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function addDays(isoDate, count) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Invalid date: ${isoDate}`);
  }
  return new Date(date.getTime() + count * DAY_MS).toISOString().slice(0, 10);
}

export function selectInitialWeek(weeks, today) {
  if (!Array.isArray(weeks) || weeks.length === 0) return null;
  const ordered = [...weeks].sort((a, b) => a.starts_on.localeCompare(b.starts_on));
  const containing = ordered.find(
    (week) => week.starts_on <= today && today <= addDays(week.starts_on, 6),
  );
  if (containing) return containing.starts_on;
  const future = ordered.find((week) => week.starts_on > today);
  return future?.starts_on ?? ordered.at(-1).starts_on;
}

function subgroupNumber(subgroup) {
  const match = String(subgroup ?? "").match(/^\s*([12])\b/);
  return match?.[1] ?? null;
}

export function classMatchesFilter(event, filter) {
  if (filter === "all") return true;
  const subgroup = subgroupNumber(event.subgroup);
  return subgroup === null || subgroup === String(filter);
}

export function filterClasses(classes, filter) {
  return classes.filter((event) => classMatchesFilter(event, filter));
}

export function flattenClasses(timetable, filter = "all") {
  const events = [];
  for (const week of timetable.weeks ?? []) {
    for (const day of week.days ?? []) {
      for (const event of filterClasses(day.classes ?? [], filter)) {
        events.push({ ...event, date: day.date, week_start: week.starts_on });
      }
    }
  }
  return events.sort((a, b) => {
    const timeOrder = Date.parse(a.starts_at) - Date.parse(b.starts_at);
    if (timeOrder !== 0) return timeOrder;
    return String(a.subgroup ?? "").localeCompare(String(b.subgroup ?? ""));
  });
}

export function getLiveState(timetable, filter = "all", now = new Date()) {
  const nowTime = now.getTime();
  const events = flattenClasses(timetable, filter);
  const current = events.find((event) => {
    const start = Date.parse(event.starts_at);
    const end = Date.parse(event.ends_at);
    return start <= nowTime && nowTime < end;
  });
  if (current) {
    return {
      kind: "current",
      event: current,
      minutes: Math.max(0, Math.ceil((Date.parse(current.ends_at) - nowTime) / 60000)),
    };
  }
  const next = events.find((event) => Date.parse(event.starts_at) > nowTime);
  if (next) {
    return {
      kind: "next",
      event: next,
      minutes: Math.max(0, Math.ceil((Date.parse(next.starts_at) - nowTime) / 60000)),
    };
  }
  return { kind: "none", event: null, minutes: null };
}

function escapeIcs(value) {
  return String(value ?? "")
    .replaceAll("\\", "\\\\")
    .replaceAll("\n", "\\n")
    .replaceAll(";", "\\;")
    .replaceAll(",", "\\,");
}

function icsDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error(`Invalid event date: ${value}`);
  return date.toISOString().replaceAll("-", "").replaceAll(":", "").replace(/\.\d{3}Z$/, "Z");
}

function stableHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function buildIcs(event, groupId = "653", createdAt = new Date()) {
  const key = [
    event.starts_at,
    event.ends_at,
    event.subject,
    event.teacher,
    event.room,
    event.subgroup,
  ].join("|");
  const description = [
    event.teacher ? `Преподаватель: ${event.teacher}` : null,
    event.subgroup ? `Подгруппа: ${event.subgroup}` : null,
    `Группа БГУ ${groupId}`,
  ].filter(Boolean).join("\n");
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//BSU Timetable//Group 653//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${stableHash(key)}@bsu-${groupId}.local`,
    `DTSTAMP:${icsDate(createdAt.toISOString())}`,
    `DTSTART:${icsDate(event.starts_at)}`,
    `DTEND:${icsDate(event.ends_at)}`,
    `SUMMARY:${escapeIcs(event.subject)}`,
    `DESCRIPTION:${escapeIcs(description)}`,
    `LOCATION:${escapeIcs(event.room)}`,
    "END:VEVENT",
    "END:VCALENDAR",
    "",
  ].join("\r\n");
}
