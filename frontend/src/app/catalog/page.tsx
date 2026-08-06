import type { Metadata } from "next";
import "./catalog.css";
import CatalogClient from "./CatalogClient";

export const metadata: Metadata = {
  title: "Data — BODP",
};

export default function CatalogPage() {
  return <CatalogClient />;
}
