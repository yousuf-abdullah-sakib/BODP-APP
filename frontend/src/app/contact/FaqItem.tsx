"use client";

import { useState } from "react";

export default function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`faq-item${open ? " open" : ""}`} onClick={() => setOpen((o) => !o)}>
      <div className="faq-q">
        {q} <span>+</span>
      </div>
      <div className="faq-a">{a}</div>
    </div>
  );
}
