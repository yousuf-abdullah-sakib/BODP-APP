"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useTheme } from "@/context/ThemeContext";
import { useSession } from "@/context/SessionContext";

const NAV_LINKS = [
  { href: "/", label: "Home" },
  { href: "/catalog", label: "Data" },
  { href: "/visualize", label: "Visualization" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
  { href: "/blog", label: "Blog" },
];

export default function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const { user } = useSession();
  const [open, setOpen] = useState(false);

  const accountLabel = user ? user.full_name : "My Account";
  const isDashboardArea = pathname?.startsWith("/admin") || pathname?.startsWith("/dashboard");

  const goToAccount = () => {
    setOpen(false);
    if (user) {
      router.push(user.role === "admin" ? "/admin" : "/dashboard");
    } else {
      router.push("/login");
    }
  };

  if (isDashboardArea) {
    return (
      <nav>
        <div className="nav-inner">
          <Link href="/" className="nav-brand">
            BODP
          </Link>
          <div className="nav-right">
            <Link href="/" className="btn-nav">
              Visit Site →
            </Link>
          </div>
        </div>
      </nav>
    );
  }

  return (
    <nav>
      <div className="nav-inner">
        <Link href="/" className="nav-brand">
          BODP
        </Link>
        <ul className={`nav-links${open ? " open" : ""}`}>
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className={pathname === link.href ? "active" : ""}
                onClick={() => setOpen(false)}
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="nav-right">
          <button
            className="theme-toggle"
            onClick={toggleTheme}
            title="Toggle theme"
            aria-label="Toggle theme"
          >
            {theme === "light" ? "🌙" : "☀️"}
          </button>
          <button className="btn-nav" onClick={goToAccount}>
            {accountLabel}
          </button>
          <button className="hamburger" onClick={() => setOpen((o) => !o)} aria-label="Menu">
            <span></span>
            <span></span>
            <span></span>
          </button>
        </div>
      </div>
    </nav>
  );
}
