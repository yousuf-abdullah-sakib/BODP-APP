"use client";

import { useEffect, useState } from "react";
import { getTeamMembers } from "@/lib/api/content";
import type { PublicTeamMember } from "@/lib/types/content";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function AboutTeamSection() {
  const [team, setTeam] = useState<PublicTeamMember[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getTeamMembers()
      .then((members) => setTeam([...members].sort((a, b) => a.display_order - b.display_order)))
      .catch(() => setTeam([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <section style={{ background: "var(--bg-secondary)" }}>
      <div className="section-tag">Our Team</div>
      <h2 className="section-title">The People Behind BODP</h2>
      <p className="section-sub">
        A multidisciplinary team of oceanographers, data scientists, and engineers.
      </p>
      <div className="team-grid">
        {team.map((m) => (
          <div className="team-card" key={m.id}>
            {m.photo_url ? (
              <img src={m.photo_url} alt={m.name} className="team-avatar-photo" />
            ) : (
              <div className="team-avatar">{initials(m.name)}</div>
            )}
            <div className="team-name">{m.name}</div>
            {m.role && <div className="team-role">{m.role}</div>}
            {m.bio && <div className="team-desc">{m.bio}</div>}
          </div>
        ))}
        {!loading && team.length === 0 && (
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
            Team information is being updated.
          </p>
        )}
      </div>
    </section>
  );
}
