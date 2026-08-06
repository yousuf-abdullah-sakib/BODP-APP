import type { Metadata } from "next";
import { notFound } from "next/navigation";
import "../catalog.css";
import { ApiError } from "@/lib/api/client";
import { getDatasetDetail } from "@/lib/api/catalog";
import DatasetDetailClient from "./DatasetDetailClient";

export const metadata: Metadata = {
  title: "Dataset — BODP",
};

export default async function DatasetDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  try {
    const dataset = await getDatasetDetail(id);
    return <DatasetDetailClient dataset={dataset} />;
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
      notFound();
    }
    throw err;
  }
}
