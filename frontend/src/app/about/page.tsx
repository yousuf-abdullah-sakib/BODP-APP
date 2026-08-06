"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import "./about.css";
import AboutTeamSection from "./AboutTeamSection";
import { getCmsBlocks, blocksToMap } from "@/lib/api/content";

const WHAT_WE_DO = [
  {
    icon: "📡",
    title: "Data Collection",
    desc: "Operating and maintaining monitoring stations that record oceanographic and atmospheric parameters continuously.",
  },
  {
    icon: "🔬",
    title: "Quality Control",
    desc: "Automated QA pipelines and expert manual review ensure every published record meets international data-quality standards.",
  },
  {
    icon: "🗄️",
    title: "Secure Archiving",
    desc: "Long-term storage with versioned backups, metadata cataloguing, and compliance with FAIR data principles.",
  },
  {
    icon: "🌐",
    title: "Open Access Distribution",
    desc: "Controlled, role-based release of datasets to approved researchers through a transparent request-and-approval workflow.",
  },
  {
    icon: "📊",
    title: "Visualisation & Analysis",
    desc: "Interactive time-series charts, GIS thematic maps, and spatial interpolation tools built into the portal.",
  },
  {
    icon: "🤝",
    title: "Capacity Building",
    desc: "Training workshops, documentation, and API access for institutions building their own data-driven tools.",
  },
];

const TIMELINE = [
  {
    year: "2020",
    title: "Founding Partnership",
    desc: "BORI, DoE, BUET, and CUET sign a Memorandum of Understanding to jointly develop a national oceanographic data portal.",
  },
  {
    year: "2021",
    title: "Station Deployment & Platform Development",
    desc: "Coastal monitoring stations installed from Cox's Bazar to the Sundarbans. Portal platform development begins with IOC-UNESCO technical support.",
  },
  {
    year: "2022",
    title: "Public Beta Launch",
    desc: "BODP launches with an initial catalogue of records across Pollution, Environmental, and Hydrological categories.",
  },
  {
    year: "2023",
    title: "Expansion of Categories & Stations",
    desc: "Water Quality, Atmospheric, and Model Data categories added, alongside additional offshore buoy stations.",
  },
  {
    year: "2024",
    title: "API Release",
    desc: "Public REST API launched for programmatic data access, alongside interactive visualisation tools.",
  },
  {
    year: "2026",
    title: "Portal Modernisation",
    desc: "Rebuilt public portal and dashboard experience, with expanded self-service dataset request and visualisation tooling.",
  },
];

const PARTNERS = [
  { icon: "🌊", name: "BORI", type: "Lead Scientific Partner" },
  { icon: "🏛️", name: "Dept. of Environment", type: "Government Partner" },
  { icon: "🎓", name: "BUET", type: "Academic Partner" },
  { icon: "🎓", name: "CUET", type: "Academic Partner" },
  { icon: "🌍", name: "IOC-UNESCO", type: "International Partner" },
  { icon: "🌱", name: "UNDP Bangladesh", type: "Development Partner" },
  { icon: "🏭", name: "Ministry of Fisheries", type: "Government Partner" },
  { icon: "🔬", name: "BFRI", type: "Research Partner" },
];

const DEFAULTS: Record<string, string> = {
  "about.hero.title": "About the Portal",
  "about.hero.subtitle":
    "Building open access to Bangladesh's oceanographic and environmental data.",
  "about.mission.title": "Our Mission",
  "about.mission.body":
    "To make marine, coastal, and environmental data from the Bay of Bengal freely and easily accessible to researchers, policymakers, and the public.",
};

export default function AboutPage() {
  const [map, setMap] = useState<Record<string, string>>({});

  useEffect(() => {
    getCmsBlocks("about")
      .then((blocks) => setMap(blocksToMap(blocks)))
      .catch(() => {
        /* keep defaults on failure */
      });
  }, []);

  const t = (key: string) => map[key] || DEFAULTS[key] || "";

  return (
    <>
      <div className="page-hero">
        <div className="section-tag">About BODP</div>
        <h1>{t("about.hero.title")}</h1>
        <p>{t("about.hero.subtitle")}</p>
      </div>

      <div className="mv-band">
        <div className="mv-grid">
          <div className="mv-card">
            <div className="mv-icon">🎯</div>
            <div className="mv-title">{t("about.mission.title")}</div>
            <div className="mv-body">{t("about.mission.body")}</div>
          </div>
          <div className="mv-card">
            <div className="mv-icon">🔭</div>
            <div className="mv-title">Our Vision</div>
            <div className="mv-body">
              A Bangladesh where every policy decision affecting the coast and ocean is
              grounded in high-quality data; where researchers and communities have
              equal access to the environmental information they need; and where the Bay
              of Bengal is managed as a shared, sustainable resource for future
              generations.
            </div>
          </div>
        </div>
      </div>

      <section>
        <div style={{ maxWidth: 820, margin: "0 auto" }}>
          <div className="section-tag">Our Story</div>
          <h2 className="section-title">Built for Bangladesh&apos;s Oceans</h2>
          <p className="section-sub" style={{ maxWidth: "100%" }}>
            From a research initiative to a national data infrastructure.
          </p>
          <div className="prose" style={{ marginTop: "1.5rem" }}>
            <p>
              The Bangladesh Oceanographic Data Portal (BODP) was conceived as a
              collaborative initiative between the Bangladesh Oceanographic Research
              Institute (BORI), the Department of Environment (DoE), and leading
              technical universities including BUET and CUET. The founding
              institutions recognised a critical gap: vast quantities of valuable
              marine and coastal data were being collected by different agencies but
              stored in silos, inaccessible to the wider research community.
            </p>
            <p>
              Development began with a mandate to build a secure, role-based data
              portal that would aggregate measurements from monitoring stations
              across Bangladesh&apos;s coastline — from Teknaf in the south to the
              Meghna estuary in the north — as well as offshore stations in the Bay of
              Bengal.
            </p>
            <p>
              Today BODP hosts a growing catalogue of curated records across
              multiple scientific categories, serving researchers, government
              agencies, and NGOs alike. The portal is governed by a steering
              committee and follows open-data principles under the Creative Commons
              Attribution 4.0 licence.
            </p>
          </div>
        </div>
      </section>

      <section style={{ background: "var(--bg-secondary)" }}>
        <div className="section-tag">What We Do</div>
        <h2 className="section-title">Core Activities</h2>
        <p className="section-sub">
          Interlocking programmes that keep the portal running and the data
          flowing.
        </p>
        <div className="what-grid">
          {WHAT_WE_DO.map((w) => (
            <div className="what-item" key={w.title}>
              <div className="what-icon">{w.icon}</div>
              <div className="what-text">
                <h4>{w.title}</h4>
                <p>{w.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div style={{ maxWidth: 680 }}>
          <div className="section-tag">History</div>
          <h2 className="section-title">Key Milestones</h2>
          <p className="section-sub">
            From concept to national infrastructure.
          </p>
          <div className="timeline">
            {TIMELINE.map((t) => (
              <div className="tl-item" key={t.year}>
                <div className="tl-year">{t.year}</div>
                <div className="tl-title">{t.title}</div>
                <div className="tl-desc">{t.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <AboutTeamSection />

      <section>
        <div className="section-tag">Partners</div>
        <h2 className="section-title">Institutional Partners</h2>
        <p className="section-sub">
          BODP is a consortium effort supported by government, academia, and
          international organisations.
        </p>
        <div className="partner-logos">
          {PARTNERS.map((p) => (
            <div className="partner-logo-card" key={p.name}>
              <div className="pl-icon">{p.icon}</div>
              <div>
                <div className="pl-name">{p.name}</div>
                <div className="pl-type">{p.type}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="cta-band">
        <div className="section-tag">Get Involved</div>
        <h2>Ready to Access the Data?</h2>
        <p>
          Register for a free researcher account and submit your first dataset
          request within minutes.
        </p>
        <div className="cta-btns">
          <Link href="/login" className="btn-primary">
            Create an Account →
          </Link>
          <Link href="/catalog" className="btn-outline">
            Browse Datasets
          </Link>
        </div>
      </div>
    </>
  );
}
