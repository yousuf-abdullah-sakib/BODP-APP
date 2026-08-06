// Mirrors backend/app/schemas/admin_cms.py exactly — keep these two in sync.

export interface CmsBlockCreate {
  key: string;
  page: string;
  section?: string | null;
  label?: string | null;
  value?: string | null;
  display_order?: number;
  is_active?: boolean;
}

export interface CmsBlockUpdate {
  section?: string | null;
  label?: string | null;
  value?: string | null;
  display_order?: number | null;
  is_active?: boolean | null;
}

export interface CmsBlockPublic {
  id: string;
  key: string;
  page: string;
  section: string | null;
  label: string | null;
  value: string | null;
  display_order: number;
  is_system_block: boolean;
  is_active: boolean;
  updated_at: string;
}

// The 6 Master-Plan-named public pages that get a Site Content tab in the
// admin UI. Legal pages are managed the same way but grouped separately
// since there are 6 of them, all sharing one generic public template.
export const CMS_PAGES = ["home", "about", "contact", "blog", "footer"] as const;

export const LEGAL_PAGES: { slug: string; label: string }[] = [
  { slug: "terms-conditions", label: "Terms & Conditions" },
  { slug: "privacy-policy", label: "Privacy Policy" },
  { slug: "download-policy", label: "Download Policy" },
  { slug: "citation-policy", label: "Citation Policy" },
  { slug: "cookie-policy", label: "Cookie Policy" },
  { slug: "disclaimer", label: "Disclaimer" },
];
