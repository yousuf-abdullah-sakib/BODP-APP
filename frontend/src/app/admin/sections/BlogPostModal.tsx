"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import MediaPicker from "@/components/ui/MediaPicker";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createBlogPost, updateBlogPost } from "@/lib/api/admin-blog";
import { BLOG_CATEGORIES } from "@/lib/types/admin-blog";
import type { BlogPostAdminDetail, BlogPostStatus } from "@/lib/types/admin-blog";
import type { MediaFilePublic } from "@/lib/types/admin-media";

interface BlogPostModalProps {
  post: BlogPostAdminDetail | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function BlogPostModal({ post, onClose, onSaved }: BlogPostModalProps) {
  const { toast } = useToast();
  const isNew = !post;
  const [title, setTitle] = useState(post?.title ?? "");
  const [category, setCategory] = useState(post?.category ?? BLOG_CATEGORIES[0]);
  const [authorName, setAuthorName] = useState(post?.author_name ?? "");
  const [excerpt, setExcerpt] = useState(post?.excerpt ?? "");
  const [content, setContent] = useState(post?.content_html ?? "");
  const [status, setStatus] = useState<BlogPostStatus>(post?.status ?? "draft");
  const [featured, setFeatured] = useState(post?.featured ?? false);
  const [imageId, setImageId] = useState<string | null>(post?.featured_image_id ?? null);
  const [imageUrl, setImageUrl] = useState<string | null>(post?.featured_image_url ?? null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  function handlePick(media: MediaFilePublic) {
    setImageId(media.id);
    setImageUrl(media.url);
    setPickerOpen(false);
  }

  async function save() {
    if (!title.trim()) {
      toast("Post title is required.", "error");
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        await createBlogPost({
          title: title.trim(),
          category,
          author_name: authorName.trim() || null,
          excerpt: excerpt.trim() || null,
          content_html: content,
          status,
          featured,
          featured_image_id: imageId,
        });
      } else {
        await updateBlogPost(post.id, {
          title: title.trim(),
          category,
          author_name: authorName.trim() || null,
          excerpt: excerpt.trim() || null,
          content_html: content,
          featured,
          featured_image_id: imageId,
        });
      }
      toast(status === "published" ? "Post published." : "Draft saved.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save post.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Modal
        title={isNew ? "Add Blog Post" : "Edit Blog Post"}
        onClose={onClose}
        large
        footer={
          <>
            <button className="btn-cancel" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn-submit" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save Post"}
            </button>
          </>
        }
      >
        <div className="form-group">
          <label className="form-label">Title *</label>
          <input className="form-input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label className="form-label">Category</label>
            <select className="form-select" value={category ?? ""} onChange={(e) => setCategory(e.target.value)}>
              {BLOG_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Author</label>
            <input
              className="form-input"
              value={authorName}
              onChange={(e) => setAuthorName(e.target.value)}
              placeholder="Defaults to your account name"
            />
          </div>
        </div>
        <div className="form-group">
          <label className="form-label">Featured Image</label>
          <div
            style={{
              border: "2px dashed var(--border)",
              borderRadius: 8,
              padding: imageUrl ? "0.6rem" : "1.2rem",
              textAlign: "center",
              cursor: "pointer",
              fontSize: "0.82rem",
              color: "var(--text-muted)",
            }}
            onClick={() => setPickerOpen(true)}
          >
            {imageUrl ? (
              <img
                src={imageUrl}
                alt="Featured"
                style={{ maxHeight: 120, borderRadius: 6, display: "block", margin: "0 auto" }}
              />
            ) : (
              "🖼️ Click to choose a featured image from the Media Library"
            )}
          </div>
          {imageUrl && (
            <button
              className="btn-clear-spatial"
              style={{ marginTop: "0.5rem" }}
              onClick={() => {
                setImageId(null);
                setImageUrl(null);
              }}
            >
              Remove image
            </button>
          )}
        </div>
        <div className="form-group">
          <label className="form-label">Excerpt</label>
          <textarea
            className="form-textarea"
            style={{ minHeight: 60 }}
            value={excerpt}
            onChange={(e) => setExcerpt(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label className="form-label">Content (HTML supported)</label>
          <textarea
            className="form-textarea"
            style={{ minHeight: 180 }}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <div style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginTop: "0.4rem" }}>
            Sanitized automatically on save — scripts and inline event handlers are stripped.
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label className="form-label">Status</label>
            <select
              className="form-select"
              value={status}
              onChange={(e) => setStatus(e.target.value as BlogPostStatus)}
            >
              <option value="published">Published</option>
              <option value="draft">Draft</option>
            </select>
          </div>
          <div className="form-group">
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer", marginTop: "1.6rem" }}>
              <input type="checkbox" checked={featured} onChange={(e) => setFeatured(e.target.checked)} />
              Featured post
            </label>
          </div>
        </div>
      </Modal>

      {pickerOpen && <MediaPicker onClose={() => setPickerOpen(false)} onSelect={handlePick} />}
    </>
  );
}
