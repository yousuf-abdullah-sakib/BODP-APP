"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardShell, { type DashNavGroup } from "@/components/dashboard/DashboardShell";
import { getUnreadCount } from "@/lib/api/me";
import MyDatasetsSection from "./sections/MyDatasetsSection";
import RequestsSection from "./sections/RequestsSection";
import OverviewSection from "./sections/OverviewSection";
import NotificationsSection from "./sections/NotificationsSection";
import ProfileSection from "./sections/ProfileSection";
import SecuritySection from "./sections/SecuritySection";
import PreferencesSection from "./sections/PreferencesSection";
import HelpCenterSection from "./sections/HelpCenterSection";
import GuidelinesSection from "./sections/GuidelinesSection";
import ContactSupportSection from "./sections/ContactSupportSection";

const NAV_GROUPS: DashNavGroup[] = [
  {
    title: "Main",
    items: [
      { key: "overview", label: "Overview", icon: "🏠" },
      { key: "requests", label: "My Requests", icon: "📨" },
      { key: "datasets", label: "My Datasets", icon: "📁" },
    ],
  },
  {
    title: "Account",
    items: [
      { key: "notifications", label: "Notifications", icon: "🔔" },
      { key: "profile", label: "Profile", icon: "👤" },
      { key: "security", label: "Security", icon: "🔒" },
      { key: "preferences", label: "Preferences", icon: "⚙️" },
    ],
  },
  {
    title: "Support",
    items: [
      { key: "help", label: "Help Center", icon: "❓" },
      { key: "guidelines", label: "Guidelines", icon: "📜" },
      { key: "contact", label: "Contact Support", icon: "✉️" },
    ],
  },
];

export default function DashboardClient() {
  const [active, setActive] = useState("overview");
  const [unreadCount, setUnreadCount] = useState(0);

  const refreshUnreadCount = useCallback(() => {
    getUnreadCount()
      .then((data) => setUnreadCount(data.unread_count))
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshUnreadCount();
  }, [refreshUnreadCount]);

  const groupsWithBadge = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.map((item) =>
      item.key === "notifications" ? { ...item, badge: unreadCount } : item
    ),
  }));

  return (
    <DashboardShell
      groups={groupsWithBadge}
      activeKey={active}
      onNavigate={setActive}
      showTopBar
      unreadNotifCount={unreadCount}
      onBellClick={() => setActive("notifications")}
    >
      {active === "overview" && <OverviewSection onNavigate={setActive} />}
      {active === "requests" && <RequestsSection />}
      {active === "datasets" && <MyDatasetsSection />}
      {active === "notifications" && (
        <NotificationsSection onUnreadCountChange={setUnreadCount} />
      )}
      {active === "profile" && <ProfileSection />}
      {active === "security" && <SecuritySection />}
      {active === "preferences" && <PreferencesSection />}
      {active === "help" && <HelpCenterSection />}
      {active === "guidelines" && <GuidelinesSection />}
      {active === "contact" && <ContactSupportSection />}
    </DashboardShell>
  );
}
