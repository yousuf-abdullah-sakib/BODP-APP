"use client";

import { useState } from "react";
import DashboardShell, { type DashNavGroup } from "@/components/dashboard/DashboardShell";
import MyDatasetsSection from "./sections/MyDatasetsSection";
import RequestsSection from "./sections/RequestsSection";

const NAV_GROUPS: DashNavGroup[] = [
  {
    title: "Main",
    items: [
      { key: "requests", label: "My Requests", icon: "📨" },
      { key: "datasets", label: "My Datasets", icon: "📁" },
    ],
  },
];

export default function DashboardClient() {
  const [active, setActive] = useState("requests");

  return (
    <DashboardShell groups={NAV_GROUPS} activeKey={active} onNavigate={setActive}>
      {active === "requests" && <RequestsSection />}
      {active === "datasets" && <MyDatasetsSection />}
    </DashboardShell>
  );
}
