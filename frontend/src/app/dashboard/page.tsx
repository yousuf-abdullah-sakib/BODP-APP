"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import "./dashboard.css";
import { useSession } from "@/context/SessionContext";
import DashboardClient from "./DashboardClient";

export default function DashboardPage() {
  const { user, loading } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="empty-state" style={{ padding: "4rem 0" }}>
        <div className="es-icon">⏳</div>
        <p>Loading your dashboard…</p>
      </div>
    );
  }

  return <DashboardClient />;
}
