"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import MediaPicker from "@/components/ui/MediaPicker";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createAboutTeamMember, updateAboutTeamMember } from "@/lib/api/admin-about-team";
import type { AboutTeamMemberPublic } from "@/lib/types/admin-about-team";
import type { MediaFilePublic } from "@/lib/types/admin-media";

interface AdminAboutTeamModalProps {
  member: AboutTeamMemberPublic | null;
  nextOrder: number;
  onClose: () => void;
  onSaved: () => void;
}

export default function AdminAboutTeamModal({
  member,
  nextOrder,
  onClose,
  onSaved,
}: AdminAboutTeamModalProps) {
  const { toast } = useToast();
  const isNew = !member;
  const [name, setName] = useState(member?.name ?? "");
  const [role, setRole] = useState(member?.role ?? "");
  const [bio, setBio] = useState(member?.bio ?? "");
  const [photoId, setPhotoId] = useState<string | null>(member?.photo_id ?? null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(member?.photo_url ?? null);
  const [order, setOrder] = useState(member?.display_order ?? nextOrder);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  function handlePick(media: MediaFilePublic) {
    setPhotoId(media.id);
    setPhotoUrl(media.url);
    setPickerOpen(false);
  }

  async function save() {
    if (!name.trim() || !role.trim()) {
      toast("Name and designation are required.", "error");
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        await createAboutTeamMember({
          name: name.trim(),
          role: role.trim(),
          bio: bio.trim() || null,
          photo_id: photoId,
          display_order: order,
        });
      } else {
        await updateAboutTeamMember(member.id, {
          name: name.trim(),
          role: role.trim(),
          bio: bio.trim() || null,
          photo_id: photoId,
          display_order: order,
        });
      }
      toast(isNew ? "Team member added." : "Team member updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save team member.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Modal
        title={isNew ? "Add Team Member" : "Edit Team Member"}
        onClose={onClose}
        footer={
          <>
            <button className="btn-cancel" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn-submit" onClick={save} disabled={saving}>
              {saving ? "Saving…" : isNew ? "Add Member" : "Save Changes"}
            </button>
          </>
        }
      >
        <div className="form-group">
          <label className="form-label">Profile Photo</label>
          <div
            style={{
              border: "2px dashed var(--border)",
              borderRadius: 8,
              padding: photoUrl ? "0.6rem" : "1.2rem",
              textAlign: "center",
              cursor: "pointer",
              fontSize: "0.82rem",
              color: "var(--text-muted)",
            }}
            onClick={() => setPickerOpen(true)}
          >
            {photoUrl ? (
              <img
                src={photoUrl}
                alt="Profile"
                style={{
                  maxHeight: 110,
                  borderRadius: "50%",
                  aspectRatio: "1 / 1",
                  objectFit: "cover",
                  display: "block",
                  margin: "0 auto",
                }}
              />
            ) : (
              "🖼️ Click to choose a profile photo from the Media Library"
            )}
          </div>
          {photoUrl && (
            <button
              className="btn-clear-spatial"
              style={{ marginTop: "0.5rem" }}
              onClick={() => {
                setPhotoId(null);
                setPhotoUrl(null);
              }}
            >
              Remove photo
            </button>
          )}
        </div>

        <div className="form-group">
          <label className="form-label">Name *</label>
          <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label">Designation *</label>
          <input
            className="form-input"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="e.g. Principal Investigator"
          />
        </div>
        <div className="form-group">
          <label className="form-label">Biography</label>
          <textarea
            className="form-textarea"
            style={{ minHeight: 90 }}
            value={bio}
            onChange={(e) => setBio(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label className="form-label">Display Order</label>
          <input
            className="form-input"
            type="number"
            min={1}
            value={order}
            onChange={(e) => setOrder(Math.max(1, Number(e.target.value)))}
          />
        </div>
      </Modal>

      {pickerOpen && <MediaPicker onClose={() => setPickerOpen(false)} onSelect={handlePick} />}
    </>
  );
}
