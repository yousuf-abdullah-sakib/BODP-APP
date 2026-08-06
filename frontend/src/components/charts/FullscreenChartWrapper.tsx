"use client";

import { useEffect, useState } from "react";

export function useFullscreenChart() {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setExpanded(false);
    }
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded]);

  return { expanded, expand: () => setExpanded(true), collapse: () => setExpanded(false) };
}

export function FullscreenButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="chart-btn chart-expand-btn" title="Expand full screen" onClick={onClick}>
      ⛶ Expand
    </button>
  );
}

interface FullscreenOverlayProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function FullscreenOverlay({ title, onClose, children }: FullscreenOverlayProps) {
  return (
    <div
      className="chart-fullscreen-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="chart-fullscreen-panel">
        <div className="chart-fullscreen-head">
          <span className="chart-fullscreen-title">{title}</span>
          <button className="chart-fullscreen-close" onClick={onClose}>
            ✕ Exit Full Screen
          </button>
        </div>
        <div className="chart-fullscreen-body">{children}</div>
      </div>
    </div>
  );
}
