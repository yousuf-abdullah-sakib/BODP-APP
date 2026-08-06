"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import "../dashboard/dashboard.css";
import { useSession } from "@/context/SessionContext";
import AdminClient from "./AdminClient";

export default function AdminPage() {
  const { user, loading } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (user.role !== "admin") {
      router.replace("/dashboard");
    }
  }, [loading, user, router]);

  if (loading || !user || user.role !== "admin") {
    return (
      <div className="empty-state" style={{ padding: "4rem 0" }}>
        <div className="es-icon">⏳</div>
        <p>Loading admin panel…</p>
      </div>
    );
  }

  return <AdminClient />;
}
