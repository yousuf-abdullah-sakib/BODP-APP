const CATEGORY_CLASS: Record<string, string> = {
  Pollution: "cat-Pollution",
  Environmental: "cat-Environmental",
  "Water Quality": "cat-Water",
  Hydrological: "cat-Hydrological",
  Atmospheric: "cat-Atmospheric",
  "Model Data": "cat-Model",
};

export default function CategoryPill({ category }: { category: string | null }) {
  const cls = category ? (CATEGORY_CLASS[category] ?? "cat-Environmental") : "cat-Environmental";
  return <span className={`cat-pill ${cls}`}>{category ?? "Uncategorized"}</span>;
}
