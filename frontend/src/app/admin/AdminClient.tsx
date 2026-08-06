"use client";

import { useEffect, useState } from "react";
import DashboardShell, { type DashNavGroup } from "@/components/dashboard/DashboardShell";
import { getAdminRequests } from "@/lib/api/admin-requests";
import { getUnreadCount } from "@/lib/api/me";
import AdminDatasetsSection from "./sections/AdminDatasetsSection";
import AdminManagementSection from "./sections/AdminManagementSection";
import AdminNotificationsSection from "./sections/AdminNotificationsSection";
import AdminRequestsSection from "./sections/AdminRequestsSection";
import DataQualityCheckSection from "./sections/DataQualityCheckSection";
import DatasetCategoriesSection from "./sections/DatasetCategoriesSection";
import DataVisualizationSection from "./sections/DataVisualizationSection";
import GrantsSection from "./sections/GrantsSection";
import OverviewSection from "./sections/OverviewSection";
import RolesPermissionsSection from "./sections/RolesPermissionsSection";
import UsersSection from "./sections/UsersSection";
import VisualizationExportsSection from "./sections/VisualizationExportsSection";
import VisualizationLimitsSection from "./sections/VisualizationLimitsSection";

export default function AdminClient() {
  const [active, setActive] = useState("overview");
  const [pendingCount, setPendingCount] = useState(0);
  const [pendingCountVersion, setPendingCountVersion] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [unreadCountVersion, setUnreadCountVersion] = useState(0);

  useEffect(() => {
    getAdminRequests("pending")
      .then((r) => setPendingCount(r.length))
      .catch(() => setPendingCount(0));
  }, [pendingCountVersion]);

  useEffect(() => {
    getUnreadCount()
      .then((r) => setUnreadCount(r.unread_count))
      .catch(() => setUnreadCount(0));
  }, [unreadCountVersion]);

  const navGroups: DashNavGroup[] = [
    {
      title: "Dashboard",
      items: [
        { key: "overview", label: "Overview", icon: "📊" },
        { key: "notifications", label: "Notifications", icon: "🔔", badge: unreadCount },
      ],
    },
    {
      title: "Access Control",
      items: [
        { key: "requests", label: "Data Requests", icon: "📨", badge: pendingCount },
        { key: "grants", label: "Access Grants", icon: "🔑" },
      ],
    },
    {
      title: "Datasets",
      items: [
        { key: "datasets", label: "Datasets", icon: "🗄️" },
        { key: "categories", label: "Dataset Categories", icon: "🏷️" },
        { key: "quality", label: "Data Quality Check", icon: "🧪" },
      ],
    },
    {
      title: "People",
      items: [
        { key: "users", label: "Users", icon: "👥" },
        { key: "roles", label: "Roles & Permissions", icon: "🔐" },
        { key: "admin-team", label: "Admin Management", icon: "🛡️" },
      ],
    },
    {
      title: "Visualization",
      items: [
        { key: "viz-exports", label: "Export Settings", icon: "📤" },
        { key: "viz-limits", label: "Compute Limits", icon: "⚡" },
        { key: "viz-boundary", label: "Boundary Shapefile", icon: "🗺️" },
      ],
    },
  ];

  return (
    <DashboardShell
      groups={navGroups}
      activeKey={active}
      onNavigate={setActive}
      isAdmin
      showTopBar
      unreadNotifCount={unreadCount}
      onBellClick={() => setActive("notifications")}
    >
      {active === "overview" && <OverviewSection onNavigate={setActive} />}
      {active === "notifications" && (
        <AdminNotificationsSection onMutate={() => setUnreadCountVersion((v) => v + 1)} />
      )}
      {active === "requests" && (
        <AdminRequestsSection onMutate={() => setPendingCountVersion((v) => v + 1)} />
      )}
      {active === "grants" && <GrantsSection />}
      {active === "datasets" && <AdminDatasetsSection />}
      {active === "categories" && <DatasetCategoriesSection />}
      {active === "quality" && <DataQualityCheckSection />}
      {active === "users" && <UsersSection />}
      {active === "roles" && <RolesPermissionsSection />}
      {active === "admin-team" && <AdminManagementSection />}
      {active === "viz-exports" && <VisualizationExportsSection />}
      {active === "viz-limits" && <VisualizationLimitsSection />}
      {active === "viz-boundary" && <DataVisualizationSection />}
    </DashboardShell>
  );
}
