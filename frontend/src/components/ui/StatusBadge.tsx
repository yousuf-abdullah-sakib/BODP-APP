const LABELS: Record<string, string> = {
  normal: "✓ Normal",
  caution: "⚠ Caution",
  alert: "✗ Alert",
  approved: "Approved",
  pending: "Pending Review",
  rejected: "Rejected",
  revoked: "Revoked",
  published: "Published",
  draft: "Draft",
  active: "Active",
  suspended: "Suspended",
  healthy: "Healthy",
  warn: "Degraded",
  bad: "Down",
};

export default function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{LABELS[status] ?? status}</span>;
}
