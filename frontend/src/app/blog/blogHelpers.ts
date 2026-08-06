const TAG_CLASS: Record<string, string> = {
  Research: "tag-research",
  "Data Updates": "tag-data",
  Climate: "tag-climate",
  Policy: "tag-policy",
  "Field Reports": "tag-station",
};

export function tagClass(category: string | null): string {
  return (category && TAG_CLASS[category]) ?? "tag-research";
}

export function formatDateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function initials(name: string | null): string {
  if (!name) return "?";
  return name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
