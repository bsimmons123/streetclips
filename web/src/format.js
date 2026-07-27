/** Seconds as M:SS.d — precise enough to judge a trim, short enough to scan. */
export function stamp(seconds) {
  const total = Math.max(0, seconds || 0);
  const minutes = Math.floor(total / 60);
  const rest = (total - minutes * 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${rest}`;
}

/** Seconds as H:MM:SS, for positions inside a two-hour recording. */
export function clock(seconds) {
  const total = Math.floor(Math.max(0, seconds || 0));
  const h = Math.floor(total / 3600);
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

export function bytes(size) {
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export const category = (value) => (value || "").replace(/_/g, " ");
