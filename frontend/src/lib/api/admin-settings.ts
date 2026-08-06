import { apiFetch } from "./client";
import type { VizComputeLimits, VizExportSettings } from "@/lib/types/visualize";
import type {
  GeneralSettingsSchema,
  GeneralSettingsUpdate,
  NotificationSettingsSchema,
  NotificationSettingsUpdate,
  StorageCapacitySchema,
  StorageCapacityUpdate,
} from "@/lib/types/admin-general-settings";

export async function getVizExportSettings(): Promise<VizExportSettings> {
  return apiFetch<VizExportSettings>("/admin/settings/visualization-exports");
}

export async function updateVizExportSettings(
  data: Partial<VizExportSettings>
): Promise<VizExportSettings> {
  return apiFetch<VizExportSettings>("/admin/settings/visualization-exports", {
    method: "PATCH",
    body: data,
  });
}

export async function getVizComputeLimits(): Promise<VizComputeLimits> {
  return apiFetch<VizComputeLimits>("/admin/settings/visualization-limits");
}

// Date-range fields are already typed `number | null` on VizComputeLimits,
// so Partial<VizComputeLimits> lets a caller either omit a field (leave
// unchanged) or pass explicit null (clear that module's limit back to
// unlimited) — JSON.stringify preserves null (unlike undefined, which it
// drops), matching the backend's exclude_unset PATCH semantics.
export async function updateVizComputeLimits(
  data: Partial<VizComputeLimits>
): Promise<VizComputeLimits> {
  return apiFetch<VizComputeLimits>("/admin/settings/visualization-limits", {
    method: "PATCH",
    body: data,
  });
}

export async function getStorageCapacity(): Promise<StorageCapacitySchema> {
  return apiFetch<StorageCapacitySchema>("/admin/settings/storage-capacity");
}

export async function updateStorageCapacity(
  data: StorageCapacityUpdate
): Promise<StorageCapacitySchema> {
  return apiFetch<StorageCapacitySchema>("/admin/settings/storage-capacity", {
    method: "PATCH",
    body: data,
  });
}

export async function getGeneralSettings(): Promise<GeneralSettingsSchema> {
  return apiFetch<GeneralSettingsSchema>("/admin/settings/general");
}

export async function updateGeneralSettings(
  data: GeneralSettingsUpdate
): Promise<GeneralSettingsSchema> {
  return apiFetch<GeneralSettingsSchema>("/admin/settings/general", { method: "PATCH", body: data });
}

export async function getNotificationSettings(): Promise<NotificationSettingsSchema> {
  return apiFetch<NotificationSettingsSchema>("/admin/settings/notifications");
}

export async function updateNotificationSettings(
  data: NotificationSettingsUpdate
): Promise<NotificationSettingsSchema> {
  return apiFetch<NotificationSettingsSchema>("/admin/settings/notifications", {
    method: "PATCH",
    body: data,
  });
}
