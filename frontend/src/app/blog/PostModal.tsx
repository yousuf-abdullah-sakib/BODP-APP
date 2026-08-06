"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { getBlogPost } from "@/lib/api/content";
import { formatDateLabel, initials, tagClass } from "./blogHelpers";
import type { PublicBlogPostDetail, PublicBlogPostSummary } from "@/lib/types/content";

interface PostModalProps {
  postId: string;
  summary: PublicBlogPostSummary;
  onClose: () => void;
}

export default function PostModal({ postId, summary, onClose }: PostModalProps) {
  const { toast } = useToast();
  const [detail, setDetail] = useState<PublicBlogPostDetail | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    getBlogPost(postId)
      .then(setDetail)
      .catch(() => toast("Failed to load the full article.", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postId]);

  function copyLink() {
    navigator.clipboard?.writeText(`${window.location.origin}/blog/${postId}`);
    toast("Link copied to clipboard.", "success");
  }

  const post = detail ?? summary;

  return (
    <div
      className="post-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="post-modal">
        <div className="post-modal-head">
          {post.featured_image_url && (
            <img src={post.featured_image_url} alt="" className="pm-head-photo" />
          )}
          <button className="pm-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
          <div className="pm-head-content">
            <div className="post-eyebrow" style={{ marginBottom: "0.6rem" }}>
              {post.category && (
                <span className={`post-tag ${tagClass(post.category)}`}>{post.category}</span>
              )}
              <span className="post-date" style={{ color: "rgba(240,248,255,0.7)" }}>
                {formatDateLabel(post.created_at)}
              </span>
            </div>
            <h2>{post.title}</h2>
          </div>
        </div>
        <div className="post-modal-body">
          <div className="pm-author-bar">
            <div className="pm-author-info">
              <div className="pm-avatar">{initials(post.author_name)}</div>
              <div>
                <div className="pm-author-name">{post.author_name ?? "BODP Team"}</div>
                {detail && <div className="pm-author-role">{detail.read_time_minutes} min read</div>}
              </div>
            </div>
            <div className="pm-share">
              <button className="share-btn" onClick={copyLink}>
                🔗 Copy Link
              </button>
              <button className="share-btn" onClick={onClose}>
                ✕ Close
              </button>
            </div>
          </div>
          {detail ? (
            <div className="pm-content" dangerouslySetInnerHTML={{ __html: detail.content_html ?? "" }} />
          ) : (
            <div className="pm-content">Loading…</div>
          )}
          {post.tags.length > 0 && (
            <div className="pm-tags-row">
              <span>Tags:</span>
              {post.tags.map((t) => (
                <span key={t} className="pm-tag">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
