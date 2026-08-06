// Mirrors backend/app/schemas/content.py exactly — keep these two in sync.

export interface PublicCmsBlock {
  key: string;
  page: string;
  section: string | null;
  label: string | null;
  value: string | null;
  display_order: number;
}

export interface PublicTeamMember {
  id: string;
  name: string;
  role: string | null;
  bio: string | null;
  photo_url: string | null;
  display_order: number;
}

export interface PublicBlogPostSummary {
  id: string;
  title: string;
  category: string | null;
  tag_key: string | null;
  author_name: string | null;
  excerpt: string | null;
  featured: boolean;
  featured_image_url: string | null;
  tags: string[];
  views: number;
  created_at: string;
}

export interface PublicBlogPostDetail extends PublicBlogPostSummary {
  content_html: string | null;
  read_time_minutes: number;
}
