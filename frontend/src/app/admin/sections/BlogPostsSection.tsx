"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  deleteBlogPost,
  getAdminBlogPost,
  getAdminBlogPosts,
  publishBlogPost,
  unpublishBlogPost,
} from "@/lib/api/admin-blog";
import type { BlogPostAdminDetail, BlogPostAdminSummary } from "@/lib/types/admin-blog";
import BlogPostModal from "./BlogPostModal";

export default function BlogPostsSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [posts, setPosts] = useState<BlogPostAdminSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"" | "published" | "draft">("");
  const [editing, setEditing] = useState<BlogPostAdminDetail | null | "new">(null);

  function refetch() {
    setLoading(true);
    getAdminBlogPosts({ status_filter: filter || undefined })
      .then(setPosts)
      .catch(() => toast("Failed to load blog posts.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const publishedCount = posts.filter((p) => p.status === "published").length;
  const draftCount = posts.filter((p) => p.status === "draft").length;

  async function handleEdit(id: string) {
    try {
      const detail = await getAdminBlogPost(id);
      setEditing(detail);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to load post.", "error");
    }
  }

  async function handleDelete(post: BlogPostAdminSummary) {
    const ok = await confirm({
      title: "Delete Blog Post",
      message: (
        <>
          Permanently delete <b>&ldquo;{post.title}&rdquo;</b>?
        </>
      ),
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteBlogPost(post.id);
      toast(`${post.title} deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete post.", "error");
    }
  }

  async function handleToggle(post: BlogPostAdminSummary) {
    try {
      if (post.status === "published") {
        await unpublishBlogPost(post.id);
        toast("Post unpublished.", "success");
      } else {
        await publishBlogPost(post.id);
        toast("Post published.", "success");
      }
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update post status.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Blog Posts</div>
          <div className="dash-sub">Manage BODP Journal articles.</div>
        </div>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + Add Post
        </button>
      </div>

      <div className="filter-tab-row">
        <button className={`filter-tab-btn${filter === "" ? " active" : ""}`} onClick={() => setFilter("")}>
          All ({posts.length})
        </button>
        <button
          className={`filter-tab-btn${filter === "published" ? " active" : ""}`}
          onClick={() => setFilter("published")}
        >
          Published ({publishedCount})
        </button>
        <button
          className={`filter-tab-btn${filter === "draft" ? " active" : ""}`}
          onClick={() => setFilter("draft")}
        >
          Draft ({draftCount})
        </button>
      </div>

      {loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading posts…</p>
        </div>
      ) : posts.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">📰</div>
          <p>No blog posts yet.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Title</th>
                <th>Category</th>
                <th>Author</th>
                <th>Date</th>
                <th>Views</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {posts.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.featured_image_url ? (
                      <img
                        src={p.featured_image_url}
                        alt=""
                        style={{ width: 44, height: 32, objectFit: "cover", borderRadius: 5 }}
                      />
                    ) : (
                      <div
                        style={{
                          width: 44,
                          height: 32,
                          borderRadius: 5,
                          background: "var(--bg-secondary)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: "0.9rem",
                        }}
                      >
                        📰
                      </div>
                    )}
                  </td>
                  <td style={{ fontWeight: 600, maxWidth: 320 }}>
                    {p.title}
                    {p.featured && (
                      <span className="badge badge-approved" style={{ marginLeft: "0.5rem" }}>
                        Featured
                      </span>
                    )}
                  </td>
                  <td>{p.category && <span className="chip">{p.category}</span>}</td>
                  <td style={{ fontSize: "0.82rem" }}>{p.author_name}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {new Date(p.created_at).toLocaleDateString()}
                  </td>
                  <td>{p.views.toLocaleString()}</td>
                  <td>
                    <span className={`badge badge-${p.status === "published" ? "published" : "draft"}`}>
                      {p.status === "published" ? "● Published" : "◐ Draft"}
                    </span>
                  </td>
                  <td>
                    <div className="flex-gap">
                      <button className="btn-icon-sm" title="Edit" onClick={() => handleEdit(p.id)}>
                        ✎
                      </button>
                      <button
                        className="btn-icon-sm"
                        title={p.status === "published" ? "Unpublish" : "Publish"}
                        onClick={() => handleToggle(p)}
                      >
                        {p.status === "published" ? "◐" : "●"}
                      </button>
                      <button className="btn-icon-sm danger" title="Delete" onClick={() => handleDelete(p)}>
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && (
        <BlogPostModal
          post={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={refetch}
        />
      )}
    </>
  );
}
