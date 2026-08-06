"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getCmsBlocks } from "@/lib/api/admin-cms";
import { deleteAboutTeamMember, getAboutTeamMembers } from "@/lib/api/admin-about-team";
import { CMS_PAGES, LEGAL_PAGES } from "@/lib/types/admin-cms";
import type { CmsBlockPublic } from "@/lib/types/admin-cms";
import type { AboutTeamMemberPublic } from "@/lib/types/admin-about-team";
import CmsBlockModal from "./CmsBlockModal";
import AdminAboutTeamModal from "./AdminAboutTeamModal";

const PAGE_TABS: { key: string; label: string }[] = [
  ...CMS_PAGES.map((p) => ({ key: p, label: p.charAt(0).toUpperCase() + p.slice(1) })),
  ...LEGAL_PAGES.map((p) => ({ key: `legal-${p.slug}`, label: p.label })),
];

export default function SiteContentSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [activePage, setActivePage] = useState<string>("home");
  const [blocks, setBlocks] = useState<CmsBlockPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<CmsBlockPublic | null>(null);

  const [team, setTeam] = useState<AboutTeamMemberPublic[]>([]);
  const [editingMember, setEditingMember] = useState<AboutTeamMemberPublic | null | "new">(null);

  function refetchBlocks() {
    setLoading(true);
    getCmsBlocks(activePage)
      .then(setBlocks)
      .catch(() => toast("Failed to load content blocks.", "error"))
      .finally(() => setLoading(false));
  }

  function refetchTeam() {
    getAboutTeamMembers()
      .then(setTeam)
      .catch(() => toast("Failed to load team members.", "error"));
  }

  useEffect(() => {
    refetchBlocks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePage]);

  useEffect(() => {
    if (activePage === "about") refetchTeam();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePage]);

  async function handleDeleteMember(member: AboutTeamMemberPublic) {
    const ok = await confirm({
      title: "Remove Team Member",
      message: (
        <>
          Remove <b>{member.name}</b> from the public About page?
        </>
      ),
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteAboutTeamMember(member.id);
      toast(`${member.name} removed from the About page.`, "info");
      refetchTeam();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to remove team member.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Site Content (CMS)</div>
          <div className="dash-sub">Edit content blocks shown across the public site.</div>
        </div>
      </div>

      <div className="filter-tab-row" style={{ flexWrap: "wrap" }}>
        {PAGE_TABS.map((p) => (
          <button
            key={p.key}
            className={`filter-tab-btn${activePage === p.key ? " active" : ""}`}
            onClick={() => setActivePage(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading content…</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
          {blocks.length === 0 && (
            <div className="empty-state">
              <div className="es-icon">📄</div>
              <p>No content blocks for this page yet.</p>
            </div>
          )}
          {blocks.map((block) => (
            <div className="panel" key={block.id}>
              <div className="panel-head">
                <span className="panel-title">
                  {block.label}
                  {!block.is_active && (
                    <span className="badge badge-revoked" style={{ marginLeft: "0.5rem" }}>
                      Inactive
                    </span>
                  )}
                </span>
                <button className="btn-icon-sm" title="Edit" onClick={() => setEditing(block)}>
                  ✎
                </button>
              </div>
              <div className="panel-body">
                <div
                  style={{
                    fontSize: "0.72rem",
                    color: "var(--text-muted)",
                    marginBottom: "0.4rem",
                    fontFamily: "monospace",
                  }}
                >
                  {block.key}
                </div>
                <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  {block.value}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {activePage === "about" && (
        <div className="panel" style={{ marginTop: "1.4rem" }}>
          <div className="panel-head">
            <span className="panel-title">Team — &ldquo;The People Behind BODP&rdquo;</span>
            <button
              className="btn-primary"
              style={{ padding: "0.4rem 0.9rem", fontSize: "0.8rem" }}
              onClick={() => setEditingMember("new")}
            >
              + Add Team Member
            </button>
          </div>
          <div className="panel-body">
            <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
              Manage the researchers and staff shown in the &ldquo;Our Team&rdquo; section on the public
              About page. Changes appear on the site immediately.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Name</th>
                    <th>Designation</th>
                    <th>Order</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {team.map((m) => (
                    <tr key={m.id}>
                      <td>
                        {m.photo_url ? (
                          <img
                            src={m.photo_url}
                            alt=""
                            style={{ width: 34, height: 34, borderRadius: "50%", objectFit: "cover" }}
                          />
                        ) : (
                          <div
                            style={{
                              width: 34,
                              height: 34,
                              borderRadius: "50%",
                              background: "var(--bg-secondary)",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              fontSize: "1.1rem",
                            }}
                          >
                            👤
                          </div>
                        )}
                      </td>
                      <td style={{ fontWeight: 600 }}>{m.name}</td>
                      <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>{m.role}</td>
                      <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>{m.display_order}</td>
                      <td>
                        <div className="flex-gap">
                          <button className="btn-icon-sm" title="Edit" onClick={() => setEditingMember(m)}>
                            ✎
                          </button>
                          <button
                            className="btn-icon-sm danger"
                            title="Remove"
                            onClick={() => handleDeleteMember(m)}
                          >
                            🗑
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {team.length === 0 && (
                    <tr>
                      <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)", padding: "1.5rem" }}>
                        No team members yet — add one to show it on the About page.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <CmsBlockModal block={editing} onClose={() => setEditing(null)} onSaved={refetchBlocks} />
      )}

      {editingMember !== null && (
        <AdminAboutTeamModal
          member={editingMember === "new" ? null : editingMember}
          nextOrder={team.length ? Math.max(...team.map((m) => m.display_order)) + 1 : 1}
          onClose={() => setEditingMember(null)}
          onSaved={refetchTeam}
        />
      )}
    </>
  );
}
