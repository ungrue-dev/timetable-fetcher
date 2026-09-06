import {
  addDays,
  buildIcs,
  dateInTimeZone,
  filterClasses,
  getLiveState,
  selectInitialWeek,
  validateTimetable,
} from "./timetable-utils.mjs";

import { readSavedTimetable, saveTimetable, newestTimetable, timetableChanged } from "./timetable-cache.mjs";

const TIMEZONE = "Europe/Minsk";
const filters = new Set(["all", "1", "2"]);
let savedFilter = null;
try {
  savedFilter = localStorage.getItem("timetable-subgroup");
} catch {
  // Use the default subgroup when browser storage is unavailable.
}

const state = {
  timetable: null,
  selectedWeek: null,
  filter: filters.has(savedFilter) ? savedFilter : "all",
  refreshing: false,
};

const ui = {
  pageDate: document.querySelector("#page-date"),
  groupName: document.querySelector("#group-name"),
  connectionStatus: document.querySelector("#connection-status"),
  statusDot: document.querySelector("#status-dot"),
  fetchButton: document.querySelector("#fetch-button"),
  fetchLabel: document.querySelector("#fetch-label"),
  notice: document.querySelector("#notice"),
  noticeText: document.querySelector("#notice-text"),
  noticeRetry: document.querySelector("#notice-retry"),
  liveKicker: document.querySelector("#live-kicker"),
  liveHeading: document.querySelector("#live-heading"),
  liveCountdown: document.querySelector("#live-countdown"),
  liveDetails: document.querySelector("#live-details"),
  todayList: document.querySelector("#today-list"),
  todayCount: document.querySelector("#today-count"),
  weekNav: document.querySelector("#week-nav"),
  weekGrid: document.querySelector("#week-grid"),
  sourceUpdated: document.querySelector("#source-updated"),
  publishedAt: document.querySelector("#published-at"),
  sourceLink: document.querySelector("#source-link"),
  filterButtons: [...document.querySelectorAll("[data-filter]")],
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function dateFromIso(isoDate) {
  return new Date(`${isoDate}T12:00:00+03:00`);
}

function formatHeroDate(isoDate) {
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    timeZone: TIMEZONE,
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(dateFromIso(isoDate));
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function formatDayName(isoDate) {
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    timeZone: TIMEZONE,
    weekday: "long",
  }).format(dateFromIso(isoDate));
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function formatShortDate(isoDate) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: TIMEZONE,
    month: "short",
    day: "2-digit",
  }).format(dateFromIso(isoDate));
}

function formatStamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: TIMEZONE,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDistance(minutes) {
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} ч ${remainder} мин` : `${hours} ч`;
}

function setConnection(message, status) {
  ui.connectionStatus.textContent = message;
  ui.statusDot.dataset.state = status;
}

function showNotice(message) {
  ui.noticeText.textContent = message;
  ui.notice.hidden = false;
}

function hideNotice() {
  ui.notice.hidden = true;
}

function appendDetail(label, value) {
  const row = element("div", "detail-row");
  row.append(element("span", "detail-label", label));
  row.append(element("span", "detail-value", value || "—"));
  ui.liveDetails.append(row);
}

function renderLive() {
  if (!state.timetable) return;
  const live = getLiveState(state.timetable, state.filter, new Date());
  ui.liveDetails.replaceChildren();

  if (live.kind === "none") {
    ui.liveKicker.textContent = "Всё свободно";
    ui.liveHeading.textContent = "Будущих занятий пока нет";
    ui.liveCountdown.textContent = "Проверьте после следующего обновления";
    appendDetail("Статус", "В сохранённом расписании нет будущих занятий.");
    return;
  }

  const event = live.event;
  if (live.kind === "current") {
    ui.liveKicker.textContent = "Сейчас идёт занятие";
    ui.liveHeading.textContent = event.subject;
    ui.liveCountdown.textContent = `Осталось ${formatDistance(live.minutes)}`;
  } else {
    ui.liveKicker.textContent = "Следующее занятие";
    ui.liveHeading.textContent = event.subject;
    ui.liveCountdown.textContent = `Начало через ${formatDistance(live.minutes)}`;
  }
  appendDetail("Когда", `${formatShortDate(event.date)} / ${event.start_time}—${event.end_time}`);
  appendDetail("Аудитория", event.room);
  appendDetail("Преподаватель", event.teacher);
  if (event.subgroup) appendDetail("Подгруппа", event.subgroup);
}

function downloadCalendar(event) {
  const ics = buildIcs(event, state.timetable.group.id);
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const safeSubject = event.subject.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 36);
  link.href = url;
  link.download = `${event.date}-${event.start_time.replace(":", "")}-${safeSubject || "class"}.ics`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function calendarButton(event, compact = false) {
  const button = element("button", "calendar-button", compact ? "+ Календарь" : "В календарь");
  button.type = "button";
  button.setAttribute("aria-label", `Добавить ${event.subject} в ${event.start_time} в календарь`);
  button.addEventListener("click", () => downloadCalendar(event));
  return button;
}

function eventIsActive(event) {
  const now = Date.now();
  return Date.parse(event.starts_at) <= now && now < Date.parse(event.ends_at);
}

function renderClassRow(event) {
  const row = element("article", "class-row");
  row.dataset.active = String(eventIsActive(event));

  const time = element("div", "class-time");
  time.append(document.createTextNode(event.start_time));
  time.append(element("span", "", ` — ${event.end_time}`));

  const subject = element("h3", "class-subject", event.subject);
  const meta = element("div", "class-meta");
  meta.append(element("span", "", event.room || "Аудитория не указана"));
  meta.append(element("span", "", event.teacher || "Преподаватель не указан"));
  if (event.subgroup) meta.append(element("span", "tag", event.subgroup));

  row.append(time, subject, meta, calendarButton(event));
  return row;
}

function allDays() {
  return state.timetable.weeks.flatMap((week) => week.days);
}

function renderToday() {
  const today = dateInTimeZone(new Date(), TIMEZONE);
  const day = allDays().find((candidate) => candidate.date === today);
  const classes = filterClasses(day?.classes ?? [], state.filter);
  ui.todayList.replaceChildren();
  const countLabel = { one: "занятие", few: "занятия", many: "занятий", other: "занятия" };
  ui.todayCount.textContent = `${classes.length} ${countLabel[new Intl.PluralRules("ru").select(classes.length)]}`;

  if (classes.length === 0) {
    ui.todayList.append(element("p", "empty-state", "На сегодня занятий в этой подгруппе нет."));
    return;
  }
  for (const event of classes) ui.todayList.append(renderClassRow({ ...event, date: today }));
}

function weekLabel(weekStart) {
  return `${formatShortDate(weekStart)} — ${formatShortDate(addDays(weekStart, 6))}`;
}

function renderWeekNavigation() {
  ui.weekNav.replaceChildren();
  state.timetable.weeks.forEach((week) => {
    const button = element("button", "week-button");
    button.type = "button";
    button.setAttribute("aria-current", String(week.starts_on === state.selectedWeek));
    button.append(element("strong", "", weekLabel(week.starts_on)));
    button.addEventListener("click", () => {
      state.selectedWeek = week.starts_on;
      renderWeekNavigation();
      renderWeek();
    });
    ui.weekNav.append(button);
  });
  const selectedButton = ui.weekNav.querySelector('[aria-current="true"]');
  if (selectedButton) {
    const centeredLeft = selectedButton.offsetLeft
      - (ui.weekNav.clientWidth - selectedButton.offsetWidth) / 2;
    ui.weekNav.scrollTo({ left: Math.max(0, centeredLeft) });
  }
}

function renderDayClass(event, date) {
  const row = element("article", "day-class");
  row.append(element("div", "day-class-time", `${event.start_time} — ${event.end_time}`));
  const body = element("div", "day-class-body");
  body.append(element("h4", "", event.subject));
  body.append(element("p", "", event.room || "Аудитория не указана"));
  body.append(element("p", "", event.teacher || "Преподаватель не указан"));
  if (event.subgroup) body.append(element("span", "tag", event.subgroup));
  row.append(body, calendarButton({ ...event, date }, true));
  return row;
}

function renderWeek() {
  ui.weekGrid.replaceChildren();
  const week = state.timetable.weeks.find((candidate) => candidate.starts_on === state.selectedWeek);
  if (!week) {
    ui.weekGrid.append(element("p", "empty-state", "Нет доступных недель расписания."));
    return;
  }
  const today = dateInTimeZone(new Date(), TIMEZONE);
  let visibleCount = 0;
  for (const day of week.days) {
    const classes = filterClasses(day.classes, state.filter);
    if (classes.length === 0) continue;
    visibleCount += classes.length;
    const card = element("section", "day-card");
    card.dataset.today = String(day.date === today);
    const header = element("header", "day-card-header");
    header.append(element("h3", "", formatDayName(day.date)));
    header.append(element("p", "", day.date === today ? `Сегодня · ${formatShortDate(day.date)}` : formatShortDate(day.date)));
    card.append(header);
    for (const event of classes) card.append(renderDayClass(event, day.date));
    ui.weekGrid.append(card);
  }
  if (visibleCount === 0) {
    ui.weekGrid.append(element("p", "empty-state", "На выбранной неделе нет занятий для этой подгруппы."));
  }
}

function renderFilters() {
  for (const button of ui.filterButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
  }
}

function render() {
  if (!state.timetable) return;
  const today = dateInTimeZone(new Date(), TIMEZONE);
  ui.pageDate.textContent = formatHeroDate(today);
  ui.groupName.textContent = state.timetable.group.name;
  ui.sourceUpdated.textContent = formatStamp(state.timetable.source_updated_at);
  ui.sourceUpdated.dateTime = state.timetable.source_updated_at;
  ui.publishedAt.textContent = formatStamp(state.timetable.published_at);
  ui.publishedAt.dateTime = state.timetable.published_at;

  if (!state.selectedWeek || !state.timetable.weeks.some((week) => week.starts_on === state.selectedWeek)) {
    state.selectedWeek = selectInitialWeek(state.timetable.weeks, today);
  }
  renderFilters();
  renderLive();
  renderToday();
  renderWeekNavigation();
  renderWeek();
}

async function loadSavedSnapshot() {
  const response = await fetch(`./timetable.json?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Сохранённое расписание пока недоступно.");
  state.timetable = newestTimetable(state.timetable, validateTimetable(await response.json()));
  saveTimetable(state.timetable);
  render();
  setConnection("Сохранённое расписание", "ready");
}

async function refreshTimetable() {
  if (state.refreshing) return;
  state.refreshing = true;
  hideNotice();
  ui.fetchButton.disabled = true;
  ui.fetchLabel.textContent = "Загрузка…";
  setConnection("Связь с БГУ", "loading");

  try {
    const response = await fetch("/api/refresh", {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Не удалось обновить расписание (${response.status}).`);
    const timetable = validateTimetable(payload.timetable);
    const changed = timetableChanged(state.timetable, timetable);
    state.timetable = timetable;
    saveTimetable(timetable);
    render();
    setConnection(changed ? "Расписание обновлено" : "Расписание актуально", "ready");
    ui.fetchLabel.textContent = changed ? "Обновлено ✓" : "Без изменений ✓";
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не удалось обновить расписание.";
    setConnection("Ошибка обновления", "error");
    showNotice(`${message} ${state.timetable ? "Показываем последнюю сохранённую копию." : ""}`);
    ui.fetchLabel.textContent = "Повторить";
  } finally {
    state.refreshing = false;
    ui.fetchButton.disabled = false;
    if (ui.fetchLabel.textContent.endsWith("✓")) {
      window.setTimeout(() => {
        if (!state.refreshing) ui.fetchLabel.textContent = "Обновить";
      }, 2600);
    }
  }
}

for (const button of ui.filterButtons) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    try {
      localStorage.setItem("timetable-subgroup", state.filter);
    } catch {
      // Filtering remains available without persistent browser storage.
    }
    render();
  });
}

ui.fetchButton.addEventListener("click", refreshTimetable);
ui.noticeRetry.addEventListener("click", refreshTimetable);

async function start() {
  state.timetable = readSavedTimetable();
  if (state.timetable) {
    render();
    setConnection("Сохранённое расписание", "ready");
  }
  try {
    await loadSavedSnapshot();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не удалось загрузить сохранённое расписание.";
    if (!state.timetable) {
      setConnection("Нет сохранённых данных", "error");
      showNotice(message);
    }
  }
  await refreshTimetable();
}

start();
window.setInterval(() => {
  if (state.timetable) {
    renderLive();
    renderToday();
  }
}, 60_000);
