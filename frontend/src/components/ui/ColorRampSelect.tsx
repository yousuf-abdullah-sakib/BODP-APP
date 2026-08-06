"use client";

import { useEffect, useRef, useState } from "react";
import type { ColorRampName } from "@/lib/geo/colorRamp";

const RAMP_OPTIONS: ColorRampName[] = ["Viridis", "Plasma", "RdBu", "YlOrRd", "Blues"];

interface ColorRampSelectProps {
  value: ColorRampName;
  onChange: (ramp: ColorRampName) => void;
  compact?: boolean;
}

export default function ColorRampSelect({ value, onChange, compact }: ColorRampSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div className={`ramp-select${compact ? " ramp-select-compact" : ""}`} ref={ref}>
      <button type="button" className="ramp-select-trigger" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}>
        <span className={`ramp-swatch legend-gradient-${value.toLowerCase()}`} />
        <span className="ramp-select-label">{value}</span>
        <span className="ramp-select-caret">▾</span>
      </button>
      {open && (
        <div className="ramp-select-menu" role="listbox">
          {RAMP_OPTIONS.map((r) => (
            <button
              type="button"
              key={r}
              className={`ramp-select-option${r === value ? " active" : ""}`}
              role="option"
              aria-selected={r === value}
              onClick={() => {
                onChange(r);
                setOpen(false);
              }}
            >
              <span className={`ramp-swatch legend-gradient-${r.toLowerCase()}`} />
              <span>{r}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
