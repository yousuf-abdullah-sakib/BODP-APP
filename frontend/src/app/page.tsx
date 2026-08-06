"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getCmsBlocks, blocksToMap } from "@/lib/api/content";
import { searchCatalog, getCatalogTaxonomy } from "@/lib/api/catalog";

const CATEGORIES = [
  {
    icon: "🏭",
    title: "Pollution Data",
    desc: "Heavy metals, microplastics, industrial effluents and chemical contaminants.",
  },
  {
    icon: "🌿",
    title: "Environmental",
    desc: "Sea surface temperature, salinity, dissolved oxygen, pH levels.",
  },
  {
    icon: "💧",
    title: "Water Quality",
    desc: "Turbidity, nutrients, coliform counts and potability metrics.",
  },
  {
    icon: "🌊",
    title: "Hydrological",
    desc: "Tidal patterns, current speeds, wave heights, sea-level data.",
  },
  {
    icon: "🌬️",
    title: "Atmospheric",
    desc: "Wind speed, humidity, air pressure and coastal CO₂ levels.",
  },
  {
    icon: "🤖",
    title: "Model Data",
    desc: "SST anomalies, storm surge forecasts, sea level rise projections.",
  },
];

const ACCESS_LEVELS = [
  {
    icon: "🎓",
    title: "Researchers & Universities",
    desc: "Apply for full dataset access for academic research and publications.",
  },
  {
    icon: "🏛️",
    title: "Government Bodies",
    desc: "Priority access for policy-making and environmental regulation.",
  },
  {
    icon: "🌱",
    title: "NGOs & Organizations",
    desc: "Access environmental data for conservation and community projects.",
  },
];

const QUICK_LINKS = [
  { href: "/catalog", icon: "📊", title: "Browse Data", desc: "Search, filter & request datasets" },
  { href: "/visualize", icon: "📈", title: "Visualize", desc: "Interactive charts & GIS maps" },
  { href: "/about", icon: "🏛️", title: "About Us", desc: "Mission, team & methodology" },
  { href: "/contact", icon: "✉️", title: "Contact", desc: "Get in touch with our team" },
];

const PARTNERS = [
  "🏛️ Department of Environment",
  "🎓 CUET",
  "🌊 BORI",
  "🏭 Ministry of Fisheries",
  "🌍 IOC-UNESCO",
  "🌱 UNDP Bangladesh",
  "🎓 BUET",
  "🔬 BFRI",
];

const MAP_DOTS = [
  { size: 10, top: 60, left: 55, delay: 0 },
  { size: 8, top: 45, left: 62, delay: 0.4 },
  { size: 12, top: 75, left: 42, delay: 0.8 },
  { size: 8, top: 30, left: 50, delay: 1.2 },
  { size: 10, top: 80, left: 68, delay: 0.3 },
  { size: 7, top: 55, left: 35, delay: 1.5 },
];

// Sensible fallback text — used until the CMS fetch resolves, and as a
// safety net if a block is ever missing/deactivated.
const DEFAULTS: Record<string, string> = {
  "home.hero.tag": "🌊 Open Ocean & Environmental Data",
  "home.hero.title": "Bangladesh Oceanographic Data Portal",
  "home.hero.subtitle":
    "Explore, visualize, and request access to marine, coastal, and environmental datasets from the Bay of Bengal.",
  "home.stats.datasets_label": "Datasets",
  "home.stats.users_label": "Researchers",
  "home.stats.downloads_label": "Downloads",
  "home.stats.institutions_label": "Institutions",
  "home.categories.title": "Explore by Category",
  "home.categories.subtitle": "Browse datasets organized by environmental domain.",
  "home.workflow.step1_title": "Browse the Catalog",
  "home.workflow.step1_body": "Search and filter datasets by category, location, or parameter.",
  "home.workflow.step2_title": "Request Access",
  "home.workflow.step2_body": "Submit a request with your research justification.",
  "home.workflow.step3_title": "Get Approved",
  "home.workflow.step3_body": "An administrator reviews and approves your request.",
  "home.workflow.step4_title": "Download & Analyze",
  "home.workflow.step4_body": "Access, visualize, and export the data you need.",
  "home.partners.intro":
    "Working with research institutions across Bangladesh and the wider Bay of Bengal region.",
};

function useCmsText() {
  const [map, setMap] = useState<Record<string, string>>({});

  useEffect(() => {
    getCmsBlocks("home")
      .then((blocks) => setMap(blocksToMap(blocks)))
      .catch(() => {
        /* keep defaults on failure */
      });
  }, []);

  return (key: string) => (map[key] || DEFAULTS[key] || "");
}

export default function HomePage() {
  const t = useCmsText();
  const [datasetCount, setDatasetCount] = useState<number | null>(null);
  const [categoryCount, setCategoryCount] = useState<number | null>(null);

  useEffect(() => {
    searchCatalog({})
      .then((res) => setDatasetCount(res.total))
      .catch(() => setDatasetCount(null));
    getCatalogTaxonomy()
      .then((tax) => setCategoryCount(tax.categories.length))
      .catch(() => setCategoryCount(null));
  }, []);

  // Only real, live numbers are shown — no invented stats. A stat card is
  // dropped entirely if there's no real source for it (Researchers,
  // Downloads and Institutions have no public count endpoint).
  const stats: { num: string; label: string }[] = [];
  if (datasetCount !== null) {
    stats.push({ num: `${datasetCount}`, label: t("home.stats.datasets_label") });
  }
  if (categoryCount !== null) {
    stats.push({ num: `${categoryCount}`, label: "Categories" });
  }

  return (
    <>
      <div className="hero">
        <div className="hero-tag">{t("home.hero.tag")}</div>
        <h1>{t("home.hero.title")}</h1>
        <p className="hero-sub">{t("home.hero.subtitle")}</p>
        <div className="hero-btns">
          <Link href="/catalog" className="btn-primary">
            Browse Datasets →
          </Link>
          <Link href="/login" className="btn-outline">
            Request Access
          </Link>
        </div>
      </div>

      {stats.length > 0 && (
        <div className="stats">
          {stats.map((s) => (
            <div className="stat-item" key={s.label}>
              <div className="stat-num">{s.num}</div>
              <div className="stat-label">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <section>
        <div className="section-tag">What We Collect</div>
        <h2 className="section-title">{t("home.categories.title")}</h2>
        <p className="section-sub">{t("home.categories.subtitle")}</p>
        <div className="features-grid">
          {CATEGORIES.map((c) => (
            <div className="feature-card" key={c.title}>
              <div className="fc-icon">{c.icon}</div>
              <div className="fc-title">{c.title}</div>
              <div className="fc-desc">{c.desc}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="workflow-section">
        <div className="section-tag">How It Works</div>
        <h2 className="section-title">Dataset Request Workflow</h2>
        <p className="section-sub">
          A secure, controlled process to ensure data is used responsibly.
        </p>
        <div className="workflow-steps">
          <div className="step">
            <div className="step-icon">🔍</div>
            <div className="step-title">{t("home.workflow.step1_title")}</div>
            <div className="step-desc">{t("home.workflow.step1_body")}</div>
          </div>
          <div className="step">
            <div className="step-icon">✍️</div>
            <div className="step-title">{t("home.workflow.step2_title")}</div>
            <div className="step-desc">{t("home.workflow.step2_body")}</div>
          </div>
          <div className="step">
            <div className="step-icon">✅</div>
            <div className="step-title">{t("home.workflow.step3_title")}</div>
            <div className="step-desc">{t("home.workflow.step3_body")}</div>
          </div>
          <div className="step">
            <div className="step-icon">⬇️</div>
            <div className="step-title">{t("home.workflow.step4_title")}</div>
            <div className="step-desc">{t("home.workflow.step4_body")}</div>
          </div>
        </div>
      </section>

      <section>
        <div className="map-section">
          <div>
            <div className="section-tag">Coverage</div>
            <h2 className="section-title">Nationwide Monitoring Network</h2>
            <p className="section-sub">
              Data collected from Cox&apos;s Bazar, Chittagong, Sundarbans, Meghna
              Estuary and other key coastal locations. Filter datasets spatially by
              drawing a region on the interactive map.
            </p>
            <br />
            <Link href="/catalog" className="btn-primary" style={{ marginTop: "1rem" }}>
              Explore Map →
            </Link>
          </div>
          <div className="map-visual">
            <div>
              <div className="map-dots">
                {MAP_DOTS.map((d, i) => (
                  <div
                    key={i}
                    className="dot"
                    style={{
                      width: d.size,
                      height: d.size,
                      top: `${d.top}%`,
                      left: `${d.left}%`,
                      animationDelay: `${d.delay}s`,
                    }}
                  />
                ))}
              </div>
              <div className="map-label">BANGLADESH COASTAL MONITORING NETWORK</div>
            </div>
          </div>
        </div>
      </section>

      <section style={{ background: "var(--bg-secondary)" }}>
        <div className="section-tag">Access Levels</div>
        <h2 className="section-title">Who Can Use BODP</h2>
        <p className="section-sub">
          Role-based access ensures data security and responsible use.
        </p>
        <div className="access-grid">
          {ACCESS_LEVELS.map((a) => (
            <div className="access-card" key={a.title}>
              <div className="ac-icon">{a.icon}</div>
              <div className="ac-title">{a.title}</div>
              <div className="ac-desc">{a.desc}</div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div style={{ textAlign: "center", marginBottom: "0.5rem" }}>
          <div className="section-tag">Quick Access</div>
        </div>
        <h2 className="section-title" style={{ textAlign: "center" }}>
          Everything You Need
        </h2>
        <div className="quick-grid">
          {QUICK_LINKS.map((q) => (
            <Link href={q.href} className="quick-item" key={q.href}>
              <div className="qi-icon">{q.icon}</div>
              <div className="qi-title">{q.title}</div>
              <div className="qi-desc">{q.desc}</div>
            </Link>
          ))}
        </div>
      </section>

      <section className="partners">
        <div className="section-tag">Partners</div>
        <h2 className="section-title">Institutional Partners</h2>
        <p className="section-sub">{t("home.partners.intro")}</p>
        <div className="partner-grid">
          {PARTNERS.map((p) => (
            <div className="partner-pill" key={p}>
              {p}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
