// Mirrors backend/app/schemas/admin_media.py exactly — keep these two in sync.

export interface MediaFileUpdate {
  alt_text?: string | null;
  title?: string | null;
}

export interface MediaFilePublic {
  id: string;
  file_name: string;
  mime_type: string | null;
  size_bytes: number | null;
  alt_text: string | null;
  title: string | null;
  url: string;
  uploaded_at: string;
}
