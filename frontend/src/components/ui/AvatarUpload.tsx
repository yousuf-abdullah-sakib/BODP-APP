"use client";

import { useRef, useState } from "react";
import { useSession } from "@/context/SessionContext";
import { useToast } from "@/context/ToastContext";
import { uploadAvatar } from "@/lib/api/me";
import { avatarUrl } from "@/lib/avatar";
import { ApiError } from "@/lib/api/client";

interface AvatarUploadProps {
  initials: string;
  variant?: "user" | "admin";
}

export default function AvatarUpload({ initials, variant = "user" }: AvatarUploadProps) {
  const { user, refreshUser } = useSession();
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!f.type.startsWith("image/")) {
      toast("Please choose an image file.", "error");
      return;
    }
    setUploading(true);
    try {
      await uploadAvatar(f);
      await refreshUser();
      toast("Profile picture updated.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to upload profile picture.", "error");
    } finally {
      setUploading(false);
    }
  }

  const src = avatarUrl(user?.avatar_key);

  return (
    <div className="avatar-upload">
      <div className={`avatar-upload-preview${variant === "admin" ? " admin" : ""}`}>
        {src ? <img src={src} alt="Profile" /> : <span>{initials}</span>}
      </div>
      <div className="avatar-upload-actions">
        <button
          className="btn-outline"
          style={{ padding: "0.4rem 0.9rem", fontSize: "0.78rem" }}
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? "Uploading…" : src ? "Change Photo" : "Upload Photo"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
      </div>
    </div>
  );
}
