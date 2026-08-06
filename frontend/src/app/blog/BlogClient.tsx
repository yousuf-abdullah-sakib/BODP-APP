"use client";

import { useEffect, useMemo, useState } from "react";
import Pagination from "@/components/ui/Pagination";
import { getBlogPosts, getCmsBlocks } from "@/lib/api/content";
import { blocksToMap } from "@/lib/api/content";
import { formatDateLabel, initials, tagClass } from "./blogHelpers";
import PostModal from "./PostModal";
import type { PublicBlogPostSummary } from "@/lib/types/content";

const CATEGORY_FILTERS = ["Research", "Data Updates", "Climate", "Policy", "Field Reports"];
const PER_PAGE = 6;

export default function BlogClient() {
  const [posts, setPosts] = useState<PublicBlogPostSummary[]>([]);
  const [intro, setIntro] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [sort, setSort] = useState<"newest" | "oldest" | "popular">("newest");
  const [page, setPage] = useState(1);
  const [openPostId, setOpenPostId] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getBlogPosts(), getCmsBlocks("blog")])
      .then(([blogPosts, blocks]) => {
        setPosts(blogPosts);
        setIntro(blocksToMap(blocks));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const featured = posts.find((p) => p.featured) ?? posts[0] ?? null;

  const categoryCounts = useMemo(() => {
    return posts.reduce<Record<string, number>>((acc, p) => {
      if (p.category) acc[p.category] = (acc[p.category] ?? 0) + 1;
      return acc;
    }, {});
  }, [posts]);

  const popular = useMemo(() => [...posts].sort((a, b) => b.views - a.views).slice(0, 5), [posts]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    let list = posts.filter((p) => {
      if (featured && p.id === featured.id) return false;
      const matchCategory = !activeCategory || p.category === activeCategory;
      const matchQ =
        !q ||
        p.title.toLowerCase().includes(q) ||
        (p.excerpt ?? "").toLowerCase().includes(q) ||
        (p.author_name ?? "").toLowerCase().includes(q) ||
        (p.category ?? "").toLowerCase().includes(q);
      return matchCategory && matchQ;
    });
    list = [...list];
    if (sort === "newest") list.sort((a, b) => b.created_at.localeCompare(a.created_at));
    if (sort === "oldest") list.sort((a, b) => a.created_at.localeCompare(b.created_at));
    if (sort === "popular") list.sort((a, b) => b.views - a.views);
    return list;
  }, [posts, featured, activeCategory, search, sort]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const pagePosts = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

  function setCategoryFilter(category: string | null) {
    setActiveCategory(category);
    setPage(1);
  }

  const openPost = openPostId ? posts.find((p) => p.id === openPostId) ?? null : null;

  if (loading) {
    return (
      <div className="empty-state" style={{ padding: "6rem 2rem" }}>
        <div className="es-icon">⏳</div>
        <p>Loading articles…</p>
      </div>
    );
  }

  return (
    <>
      <div className="blog-hero">
        <div className="blog-hero-inner">
          <div>
            <div className="section-tag">🌊 BODP Journal</div>
            <h1>
              {intro["blog.intro.title"] || "News & Insights"}
            </h1>
            <p>{intro["blog.intro.subtitle"] || "Updates on new datasets, research, and portal features."}</p>
            <div className="hero-stats">
              <div className="hero-stat">
                <b>{posts.length}</b>Articles
              </div>
              <div className="hero-stat">
                <b>{Object.keys(categoryCounts).length}</b>Categories
              </div>
            </div>
          </div>
          {featured && (
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button className="btn-primary" onClick={() => setOpenPostId(featured.id)}>
                Latest Article →
              </button>
              <button
                className="btn-outline"
                onClick={() => document.getElementById("searchBlog")?.focus()}
              >
                🔍 Search
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="blog-layout">
        <main>
          {featured && (
            <div className="featured-post" onClick={() => setOpenPostId(featured.id)}>
              <div className="featured-image">
                {featured.featured_image_url ? (
                  <img src={featured.featured_image_url} alt="" className="fi-photo" />
                ) : (
                  <div className="fi-icon">📰</div>
                )}
                <div className="fi-wave" />
              </div>
              <div className="featured-body">
                <div className="post-eyebrow">
                  <span className="post-tag tag-featured">★ Featured</span>
                  {featured.category && (
                    <span className={`post-tag ${tagClass(featured.category)}`}>{featured.category}</span>
                  )}
                  <span className="post-date">{formatDateLabel(featured.created_at)}</span>
                </div>
                <h2>{featured.title}</h2>
                <p className="post-excerpt">{featured.excerpt}</p>
                <div className="post-meta-row">
                  <div className="post-author">
                    <div className="author-avatar">{initials(featured.author_name)}</div>
                    <div>
                      <div className="author-name">{featured.author_name ?? "BODP Team"}</div>
                    </div>
                  </div>
                  <button
                    className="read-more-link"
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenPostId(featured.id);
                    }}
                  >
                    Read article →
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="search-wrap">
            <span className="search-icon">🔍</span>
            <input
              id="searchBlog"
              type="text"
              placeholder="Search articles by title, category, or author…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>

          <div className="filter-bar">
            <span className="filter-bar-label">Filter:</span>
            <button
              className={`tag-filter-btn${activeCategory === null ? " active" : ""}`}
              onClick={() => setCategoryFilter(null)}
            >
              All
            </button>
            {CATEGORY_FILTERS.map((c) => (
              <button
                key={c}
                className={`tag-filter-btn${activeCategory === c ? " active" : ""}`}
                onClick={() => setCategoryFilter(c)}
              >
                {c}
              </button>
            ))}
          </div>

          <div className="sort-row">
            <div className="sort-count">
              Showing <b>{filtered.length}</b> articles
            </div>
            <select
              className="sort-select"
              value={sort}
              onChange={(e) => setSort(e.target.value as typeof sort)}
            >
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
              <option value="popular">Most Read</option>
            </select>
          </div>

          {filtered.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">📰</div>
              <p>
                No articles match your search.
                <br />
                Try a different keyword or clear the filter.
              </p>
            </div>
          ) : (
            <div className="posts-grid">
              {pagePosts.map((p) => (
                <div className="post-card" key={p.id} onClick={() => setOpenPostId(p.id)}>
                  <div className="post-card-body">
                    <div className="post-eyebrow">
                      {p.category && <span className={`post-tag ${tagClass(p.category)}`}>{p.category}</span>}
                      <span className="post-date">{formatDateLabel(p.created_at)}</span>
                    </div>
                    <h3>{p.title}</h3>
                    <p className="post-excerpt">{p.excerpt}</p>
                    <div className="post-meta-row">
                      <div className="post-author">
                        <div className="author-avatar">{initials(p.author_name)}</div>
                        <div>
                          <div className="author-name">{p.author_name ?? "BODP Team"}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="post-card-thumb">
                    {p.featured_image_url ? (
                      <img src={p.featured_image_url} alt="" className="pct-photo" />
                    ) : (
                      "📰"
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </main>

        <aside className="blog-sidebar">
          <div className="sidebar-card">
            <div className="sidebar-title">🔥 Most Read</div>
            {popular.length === 0 ? (
              <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>No articles yet.</p>
            ) : (
              popular.map((p, i) => (
                <button className="popular-item" key={p.id} onClick={() => setOpenPostId(p.id)}>
                  <div className="popular-num">{String(i + 1).padStart(2, "0")}</div>
                  <div>
                    <div className="popular-title">{p.title}</div>
                    <div className="popular-meta">{p.views.toLocaleString()} views</div>
                  </div>
                </button>
              ))
            )}
          </div>

          <div className="sidebar-card">
            <div className="sidebar-title">📁 Categories</div>
            <div className="cat-list">
              {Object.entries(categoryCounts).map(([cat, count]) => (
                <button
                  className={`cat-item${activeCategory === cat ? " active" : ""}`}
                  key={cat}
                  onClick={() => setCategoryFilter(cat)}
                >
                  <span className="cat-name">{cat}</span>
                  <span className="cat-count">{count}</span>
                </button>
              ))}
              {Object.keys(categoryCounts).length === 0 && (
                <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>No categories yet.</p>
              )}
            </div>
          </div>
        </aside>
      </div>

      {openPost && (
        <PostModal postId={openPost.id} summary={openPost} onClose={() => setOpenPostId(null)} />
      )}
    </>
  );
}
