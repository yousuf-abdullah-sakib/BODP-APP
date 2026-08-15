"use client";

import { useEffect, useState } from "react";
import DashboardShell, { type DashNavGroup } from "@/components/dashboard/DashboardShell";
import { getAdminRequests } from "@/lib/api/admin-requests";
import { getUnreadCount } from "@/lib/api/me";
import AdminDatasetSchemaReviewSection from "./sections/AdminDatasetSchemaReviewSection";
import AdminDatasetsSection from "./sections/AdminDatasetsSection";
import AdminManagementSection from "./sections/AdminManagementSection";
import AdminNotificationsSection from "./sections/AdminNotificationsSection";
import AdminProfileSection from "./sections/AdminProfileSection";
import AdminRequestsSection from "./sections/AdminRequestsSection";
import AnalyticsSection from "./sections/AnalyticsSection";
import AuditLogSection from "./sections/AuditLogSection";
import BlogPostsSection from "./sections/BlogPostsSection";
import ContactMessagesSection from "./sections/ContactMessagesSection";
import DataQualityCheckSection from "./sections/DataQualityCheckSection";
import DatasetCategoriesSection from "./sections/DatasetCategoriesSection";
import DataVisualizationSection from "./sections/DataVisualizationSection";
import GrantsSection from "./sections/GrantsSection";
import MediaLibrarySection from "./sections/MediaLibrarySection";
import OverviewSection from "./sections/OverviewSection";
import ReportsSection from "./sections/ReportsSection";
import RolesPermissionsSection from "./sections/RolesPermissionsSection";
import SettingsSection from "./sections/SettingsSection";
import SiteContentSection from "./sections/SiteContentSection";
import SupportTicketsSection from "./sections/SupportTicketsSection";
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
        { key: "profile", label: "Profile", icon: "👤" },
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
        { key: "schema-review", label: "Schema Review", icon: "🧬" },
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
    {
      title: "Content & Reporting",
      items: [
        { key: "blog", label: "Blog Posts", icon: "📝" },
        { key: "media", label: "Media Library", icon: "🖼️" },
        { key: "content", label: "Site Content", icon: "📄" },
        { key: "analytics", label: "Analytics", icon: "📈" },
        { key: "reports", label: "Reports", icon: "🧾" },
        { key: "audit", label: "Audit Log", icon: "🕵️" },
        { key: "settings", label: "Settings", icon: "⚙️" },
      ],
    },
    {
      title: "Support",
      items: [
        { key: "contact", label: "Contact Information", icon: "✉️" },
        { key: "support-tickets", label: "Support Tickets", icon: "🎫" },
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
      {active === "profile" && <AdminProfileSection />}
      {active === "requests" && (
        <AdminRequestsSection onMutate={() => setPendingCountVersion((v) => v + 1)} />
      )}
      {active === "grants" && <GrantsSection />}
      {active === "datasets" && <AdminDatasetsSection />}
      {active === "categories" && <DatasetCategoriesSection />}
      {active === "quality" && <DataQualityCheckSection />}
      {active === "schema-review" && <AdminDatasetSchemaReviewSection />}
      {active === "users" && <UsersSection />}
      {active === "roles" && <RolesPermissionsSection />}
      {active === "admin-team" && <AdminManagementSection />}
      {active === "viz-exports" && <VisualizationExportsSection />}
      {active === "viz-limits" && <VisualizationLimitsSection />}
      {active === "viz-boundary" && <DataVisualizationSection />}
      {active === "blog" && <BlogPostsSection />}
      {active === "media" && <MediaLibrarySection />}
      {active === "content" && <SiteContentSection />}
      {active === "analytics" && <AnalyticsSection />}
      {active === "reports" && <ReportsSection />}
      {active === "audit" && <AuditLogSection />}
      {active === "settings" && <SettingsSection />}
      {active === "contact" && <ContactMessagesSection />}
      {active === "support-tickets" && <SupportTicketsSection />}
    </DashboardShell>
  );
}
