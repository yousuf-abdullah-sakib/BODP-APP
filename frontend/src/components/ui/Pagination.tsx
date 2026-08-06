interface PaginationProps {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}

export default function Pagination({ page, totalPages, onChange }: PaginationProps) {
  if (totalPages <= 1) return null;

  const start = Math.max(1, page - 3);
  const end = Math.min(totalPages, start + 6);
  const pages: number[] = [];
  for (let i = start; i <= end; i++) pages.push(i);

  return (
    <div className="pagination">
      <button className="pg-btn" disabled={page === 1} onClick={() => onChange(page - 1)}>
        ‹
      </button>
      {start > 1 && (
        <>
          <button className="pg-btn" onClick={() => onChange(1)}>
            1
          </button>
          {start > 2 && <span className="pg-info">…</span>}
        </>
      )}
      {pages.map((p) => (
        <button key={p} className={`pg-btn${p === page ? " active" : ""}`} onClick={() => onChange(p)}>
          {p}
        </button>
      ))}
      {end < totalPages && (
        <>
          {end < totalPages - 1 && <span className="pg-info">…</span>}
          <button className="pg-btn" onClick={() => onChange(totalPages)}>
            {totalPages}
          </button>
        </>
      )}
      <button className="pg-btn" disabled={page === totalPages} onClick={() => onChange(page + 1)}>
        ›
      </button>
    </div>
  );
}
