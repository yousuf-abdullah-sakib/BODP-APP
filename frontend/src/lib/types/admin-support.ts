// Mirrors backend/app/schemas/admin_support.py exactly — keep these two in sync.

export type SupportTicketStatus = "open" | "answered" | "closed";
export type SupportTicketPriority = "low" | "medium" | "high";

export interface SupportTicketReplyCreate {
  reply_message: string;
}

export interface SupportTicketAdminSummary {
  id: string;
  subject: string;
  category: string | null;
  priority: SupportTicketPriority;
  status: SupportTicketStatus;
  requester_name: string;
  requester_email: string;
  created_at: string;
}

export interface SupportTicketAdminDetail {
  id: string;
  subject: string;
  category: string | null;
  priority: SupportTicketPriority;
  status: SupportTicketStatus;
  message: string;
  requester_name: string;
  requester_email: string;
  reply_message: string | null;
  replied_by_name: string | null;
  replied_at: string | null;
  created_at: string;
}
