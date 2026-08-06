"use client";

import { useEffect, useState } from "react";
import DashboardShell, { type DashNavGroup } from "@/components/dashboard/DashboardShell";
import { getAdminRequests } from "@/lib/api/admin-requests";
import AdminRequestsSection from "./sections/AdminRequestsSection";
import GrantsSection from "./sections/GrantsSection";

export default function AdminClient() {
  const [active, setActive] = useState("requests");
  const [pendingCount, setPendingCount] = useState(0);
  const [pendingCountVersion, setPendingCountVersion] = useState(0);

  useEffect(() => {
    getAdminRequests("pending")
      .then((r) => setPendingCount(r.length))
      .catch(() => setPendingCount(0));
  }, [pendingCountVersion]);

  const navGroups: DashNavGroup[] = [
    {
      title: "Access Control",
      items: [
        { key: "requests", label: "Data Requests", icon: "📨", badge: pendingCount },
        { key: "grants", label: "Access Grants", icon: "🔑" },
      ],
    },
  ];

  return (
    <DashboardShell groups={navGroups} activeKey={active} onNavigate={setActive} isAdmin>
      {active === "requests" && (
        <AdminRequestsSection onMutate={() => setPendingCountVersion((v) => v + 1)} />
      )}
      {active === "grants" && <GrantsSection />}
    </DashboardShell>
  );
}
