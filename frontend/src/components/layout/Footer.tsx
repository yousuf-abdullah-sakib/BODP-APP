"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getCmsBlocks, blocksToMap } from "@/lib/api/content";

const FALLBACK_DESCRIPTION =
  "Bangladesh Oceanographic Data Portal — Open access marine & environmental data for the Bay of Bengal region.";
const FALLBACK_COPYRIGHT = "© 2026 Bangladesh Oceanographic Data Portal · CC BY 4.0";

export default function Footer() {
  const [description, setDescription] = useState(FALLBACK_DESCRIPTION);
  const [copyright, setCopyright] = useState(FALLBACK_COPYRIGHT);

  useEffect(() => {
    getCmsBlocks("footer")
      .then((blocks) => {
        const map = blocksToMap(blocks);
        if (map["footer.brand.description"]) setDescription(map["footer.brand.description"]);
        if (map["footer.copyright"]) setCopyright(map["footer.copyright"]);
      })
      .catch(() => {
        /* keep fallback text on failure */
      });
  }, []);

  return (
    <footer>
      <div className="footer-grid">
        <div>
          <div className="footer-brand">BODP</div>
          <div className="footer-desc">{description}</div>
        </div>
        <div className="footer-col">
          <h4>Data</h4>
          <ul>
            <li>
              <Link href="/catalog">Browse Datasets</Link>
            </li>
            <li>
              <Link href="/visualize">Visualization</Link>
            </li>
            <li>
              <Link href="/login">Request Access</Link>
            </li>
          </ul>
        </div>
        <div className="footer-col">
          <h4>Organization</h4>
          <ul>
            <li>
              <Link href="/about">About</Link>
            </li>
            <li>
              <Link href="/contact">Contact</Link>
            </li>
            <li>
              <Link href="/blog">Blog</Link>
            </li>
            <li>
              <Link href="/login">My Account</Link>
            </li>
          </ul>
        </div>
        <div className="footer-col">
          <h4>Legal</h4>
          <ul>
            <li>
              <Link href="/legal/download-policy">Data License (CC BY 4.0)</Link>
            </li>
            <li>
              <Link href="/legal/privacy-policy">Privacy Policy</Link>
            </li>
            <li>
              <Link href="/legal/terms-conditions">Terms of Use</Link>
            </li>
          </ul>
        </div>
      </div>
      <div className="footer-bottom">
        <span>{copyright}</span>
        <span>Dhaka, Bangladesh 🇧🇩</span>
      </div>
    </footer>
  );
}
