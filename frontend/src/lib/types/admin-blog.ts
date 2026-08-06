// Mirrors backend/app/schemas/admin_blog.py exactly — keep these two in sync.

export type BlogPostStatus = "draft" | "published";

export interface BlogPostCreate {
  title: string;
  category?: string | null;
  tag_key?: string | null;
  author_name?: string | null;
  excerpt?: string | null;
  content_html?: string | null;
  status?: BlogPostStatus;
  featured?: boolean;
  featured_image_id?: string | null;
  tags?: string[];
}

export interface BlogPostUpdate {
  title?: string;
  category?: string | null;
  tag_key?: string | null;
  author_name?: string | null;
  excerpt?: string | null;
  content_html?: string | null;
  featured?: boolean;
  featured_image_id?: string | null;
  tags?: string[];
}

export interface BlogPostAdminSummary {
  id: string;
  title: string;
  category: string | null;
  author_name: string | null;
  status: BlogPostStatus;
  featured: boolean;
  views: number;
  featured_image_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface BlogPostAdminDetail {
  id: string;
  title: string;
  category: string | null;
  tag_key: string | null;
  author_name: string | null;
  excerpt: string | null;
  content_html: string | null;
  status: BlogPostStatus;
  featured: boolean;
  featured_image_id: string | null;
  featured_image_url: string | null;
  tags: string[];
  views: number;
  created_at: string;
  updated_at: string;
}

export const BLOG_CATEGORIES = ["Research", "Data Updates", "Climate", "Policy", "Field Reports"];
