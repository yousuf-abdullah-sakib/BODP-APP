"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/SessionContext";
import { useConfirm } from "@/context/ConfirmContext";
import { useTheme } from "@/context/ThemeContext";

export interface DashNavItem {
  key: string;
  label: string;
  icon: string;
  badge?: number;
}

export interface DashNavGroup {
  title: string;
  items: DashNavItem[];
}

interface DashboardShellProps {
  groups: DashNavGroup[];
  activeKey: string;
  onNavigate: (key: string) => void;
  isAdmin?: boolean;
  showTopBar?: boolean;
  unreadNotifCount?: number;
  onBellClick?: () => void;
  children: React.ReactNode;
}

export default function DashboardShell({
  groups,
  activeKey,
  onNavigate,
  isAdmin,
  showTopBar,
  unreadNotifCount = 0,
  onBellClick,
  children,
}: DashboardShellProps) {
  const router = useRouter();
  const { user, logout } = useSession();
  const confirm = useConfirm();
  const { theme, toggleTheme } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [avatarMenuOpen, setAvatarMenuOpen] = useState(false);
  const avatarMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (avatarMenuRef.current && !avatarMenuRef.current.contains(e.target as Node)) {
        setAvatarMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  async function handleSignOut() {
    const ok = await confirm({
      title: "Sign Out",
      message: "Are you sure you want to sign out of BODP?",
      confirmLabel: "Sign Out",
      danger: true,
    });
    if (!ok) return;
    await logout();
    router.push("/login");
  }

  const initials = (user?.full_name ?? (isAdmin ? "Admin" : "User"))
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="dash-shell">
      <button className="dash-mobile-toggle" onClick={() => setMobileOpen(true)}>
        ☰ Menu
      </button>
      <div
        className={`dash-nav-overlay${mobileOpen ? " open" : ""}`}
        onClick={() => setMobileOpen(false)}
      />
      <aside className={`dash-nav${mobileOpen ? " open" : ""}`}>
        {isAdmin ? (
          <div className="dash-brand dash-brand-stacked">
            <div className="dash-brand-panel-label">BODP Admin Panel</div>
            <div className={`dash-avatar admin${user?.avatar_key ? " has-image" : ""}`}>
              {user?.avatar_key ? <img src={user.avatar_key} alt="" /> : initials}
            </div>
            <div className="dash-brand-role">Administrator</div>
          </div>
        ) : (
          <>
            <div className="dash-brand">
              <div className="dash-brand-mark">🌊</div>
              <div>
                <div className="dash-brand-text">BODP</div>
                <div className="dash-brand-sub">Data Portal</div>
              </div>
            </div>

            <div className="dash-user">
              <div className={`dash-avatar${user?.avatar_key ? " has-image" : ""}`}>
                {user?.avatar_key ? <img src={user.avatar_key} alt="" /> : initials}
              </div>
              <div>
                <div className="dash-user-name">{user?.full_name ?? "Guest"}</div>
                <div className="dash-user-role">{user?.institution ?? "Researcher"}</div>
              </div>
            </div>
          </>
        )}

        {groups.map((group) => (
          <div className="dash-nav-group" key={group.title}>
            <div className="dash-nav-group-title">{group.title}</div>
            {group.items.map((item) => (
              <button
                key={item.key}
                className={`dash-nav-item${activeKey === item.key ? " active" : ""}`}
                onClick={() => {
                  onNavigate(item.key);
                  setMobileOpen(false);
                }}
              >
                <span>{item.icon}</span>
                <span>{item.label}</span>
                {!!item.badge && <span className="dni-badge">{item.badge}</span>}
              </button>
            ))}
          </div>
        ))}

        <div className="dash-signout">
          <button className="btn-signout" onClick={handleSignOut}>
            ⏻ Sign Out
          </button>
        </div>
      </aside>

      <main className="dash-main">
        {showTopBar && (
          <div className="dash-topbar">
            <div className="dash-topbar-search">
              <span className="ts-icon">🔍</span>
              <input type="text" placeholder="Search anything…" />
            </div>
            <button className="dash-topbar-icon-btn" title="Notifications" onClick={onBellClick}>
              🔔
              {unreadNotifCount > 0 && <span className="tb-badge">{unreadNotifCount}</span>}
            </button>
            <button
              className="theme-toggle dash-topbar-theme-toggle"
              onClick={toggleTheme}
              title="Toggle theme"
              aria-label="Toggle theme"
            >
              {theme === "light" ? "🌙" : "☀️"}
            </button>
            <div className="dash-avatar-menu" ref={avatarMenuRef}>
              <button
                className="dash-topbar-user"
                onClick={() => setAvatarMenuOpen((o) => !o)}
                aria-haspopup="true"
                aria-expanded={avatarMenuOpen}
              >
                <div className={`tu-avatar${user?.avatar_key ? " has-image" : ""}`}>
                  {user?.avatar_key ? <img src={user.avatar_key} alt="" /> : initials}
                </div>
                <div>
                  <div className="tu-name">{isAdmin ? "Administrator" : (user?.full_name ?? "Guest")}</div>
                  <div className="tu-role">{isAdmin ? "Admin" : "Researcher"}</div>
                </div>
                <span className="tu-caret">▾</span>
              </button>
              {avatarMenuOpen && (
                <div className="dash-avatar-dropdown">
                  <div className="dash-avatar-dropdown-sep" />
                  <button onClick={handleSignOut} className="danger">
                    ⏻ Sign Out
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
