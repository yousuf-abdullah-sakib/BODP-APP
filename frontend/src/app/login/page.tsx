import type { Metadata } from "next";
import "./login.css";
import LoginClient from "./LoginClient";

export const metadata: Metadata = {
  title: "Sign In — BODP",
};

export default function LoginPage() {
  return <LoginClient />;
}
