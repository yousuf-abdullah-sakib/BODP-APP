import type { Metadata } from "next";
import "../login/login.css";
import SetPasswordClient from "./SetPasswordClient";

export const metadata: Metadata = {
  title: "Activate Account — BODP",
};

export default async function SetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return <SetPasswordClient token={token ?? null} />;
}
