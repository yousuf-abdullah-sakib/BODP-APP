import { apiFetch } from "./client";
import type { AdminOverviewResponse, StorageCapacitySchema } from "@/lib/types/admin-overview";

export async function getAdminOverview(): Promise<AdminOverviewResponse> {
  return apiFetch<AdminOverviewResponse>("/admin/overview");
}

export async function getStorageCapacity(): Promise<StorageCapacitySchema> {
  return apiFetch<StorageCapacitySchema>("/admin/settings/storage-capacity");
}

export async function updateStorageCapacity(bytes: number | null): Promise<StorageCapacitySchema> {
  return apiFetch<StorageCapacitySchema>("/admin/settings/storage-capacity", {
    method: "PATCH",
    body: { storage_capacity_bytes: bytes },
  });
}
