import { apiFetch } from "./client";
import type { BackupDownloadResponse, BackupPublic } from "@/lib/types/admin-backups";

export async function getBackups(): Promise<BackupPublic[]> {
  return apiFetch<BackupPublic[]>("/admin/backups");
}

export async function getBackup(id: string): Promise<BackupPublic> {
  return apiFetch<BackupPublic>(`/admin/backups/${id}`);
}

export async function createBackup(): Promise<BackupPublic> {
  return apiFetch<BackupPublic>("/admin/backups", { method: "POST" });
}

export async function getBackupDownloadUrl(id: string): Promise<BackupDownloadResponse> {
  return apiFetch<BackupDownloadResponse>(`/admin/backups/${id}/download`);
}
