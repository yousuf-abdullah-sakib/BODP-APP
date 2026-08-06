export type DateFormatPreference = "iso" | "dmy" | "mdy";
export type CoordinateFormatPreference = "dd" | "dms";

/** Applies the user's stored date_format preference (Preferences section). */
export function formatDate(iso: string | Date, preference: DateFormatPreference = "iso"): string {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "";

  const dd = String(date.getDate()).padStart(2, "0");
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const yyyy = date.getFullYear();

  if (preference === "dmy") return `${dd}/${mm}/${yyyy}`;
  if (preference === "mdy") return `${mm}/${dd}/${yyyy}`;
  return `${yyyy}-${mm}-${dd}`;
}

function toDMS(value: number, isLat: boolean): string {
  const abs = Math.abs(value);
  const degrees = Math.floor(abs);
  const minutesFull = (abs - degrees) * 60;
  const minutes = Math.floor(minutesFull);
  const seconds = ((minutesFull - minutes) * 60).toFixed(1);
  const hemisphere = isLat ? (value >= 0 ? "N" : "S") : value >= 0 ? "E" : "W";
  return `${degrees}°${minutes}'${seconds}"${hemisphere}`;
}

/** Applies the user's stored coordinate_format preference (Preferences section). */
export function formatCoordinate(
  value: number,
  axis: "lat" | "lon",
  preference: CoordinateFormatPreference = "dd"
): string {
  if (preference === "dms") return toDMS(value, axis === "lat");
  return `${value.toFixed(4)}°`;
}
