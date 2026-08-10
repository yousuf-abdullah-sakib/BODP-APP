// Mirrors backend/app/schemas/admin_contact.py exactly — keep these two in sync.

export type ContactSubmissionStatus = "new" | "replied";

export interface ContactReplyCreate {
  reply_message: string;
}

export interface ContactSubmissionAdminSummary {
  id: string;
  name: string;
  email: string;
  organization: string | null;
  subject: string;
  status: ContactSubmissionStatus;
  created_at: string;
}

export interface ContactSubmissionAdminDetail {
  id: string;
  name: string;
  email: string;
  organization: string | null;
  subject: string;
  message: string;
  status: ContactSubmissionStatus;
  reply_message: string | null;
  replied_by_name: string | null;
  replied_at: string | null;
  created_at: string;
}

// Mirrors ContactForm.tsx's SUBJECTS keys — the submitted `subject` field is
// the internal value (e.g. "data_request"), not the display label; this maps
// it back for admin display.
export const CONTACT_SUBJECT_LABELS: Record<string, string> = {
  data_request: "Dataset Request Help",
  technical: "Technical Support",
  partnership: "Partnership & Collaboration",
  data_quality: "Data Quality Issue",
  api: "API & Integration",
  media: "Media & Press",
  general: "General Enquiry",
};
