// Mirrors backend/app/schemas/admin_categories.py exactly — keep these two in sync.

export interface CategoryCreate {
  name: string;
  description?: string | null;
  color_tag?: string;
}

export interface CategoryUpdate {
  name?: string;
  description?: string | null;
  color_tag?: string | null;
}

export interface CategoryPublic {
  id: string;
  name: string;
  description: string | null;
  color_tag: string;
  dataset_count: number;
}
