"use client";

import { useEffect, useState } from "react";
import "./contact.css";
import ContactForm from "./ContactForm";
import FaqItem from "./FaqItem";
import { getCmsBlocks, blocksToMap } from "@/lib/api/content";

const DEPARTMENTS = [
  {
    icon: "📊",
    name: "Data & Access Team",
    email: "data@bodp.gov.bd",
    rt: "1 business day",
    desc: "Dataset request status, download issues, access approval queries, CSV/API export help.",
  },
  {
    icon: "🔧",
    name: "Technical Support",
    email: "support@bodp.gov.bd",
    rt: "1–2 business days",
    desc: "Login problems, API errors, visualisation bugs, browser compatibility issues.",
  },
  {
    icon: "🔬",
    name: "Science & Quality",
    email: "science@bodp.gov.bd",
    rt: "3 business days",
    desc: "Methodological questions, data quality reports, station calibration records, metadata corrections.",
  },
  {
    icon: "🤝",
    name: "Partnerships & Outreach",
    email: "partnerships@bodp.gov.bd",
    rt: "2–3 business days",
    desc: "Institutional collaborations, MOU enquiries, media requests, capacity-building programmes.",
  },
];

const DEFAULTS: Record<string, string> = {
  "contact.hero.title": "Get in Touch",
  "contact.hero.subtitle": "Questions about the data or your account? We're here to help.",
  "contact.info.address": "Dhaka, Bangladesh",
  "contact.info.email": "info@bodp.org",
  "contact.info.phone": "+880 000 000000",
  "contact.info.hours": "Sunday–Thursday, 9:00–17:00",
};

interface FaqEntry {
  q: string;
  a: string;
}

function parseFaqItems(raw: string | undefined): FaqEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is FaqEntry =>
        item && typeof item === "object" && typeof item.q === "string" && typeof item.a === "string"
    );
  } catch {
    return [];
  }
}

export default function ContactPage() {
  const [map, setMap] = useState<Record<string, string>>({});

  useEffect(() => {
    getCmsBlocks("contact")
      .then((blocks) => setMap(blocksToMap(blocks)))
      .catch(() => {
        /* keep defaults on failure */
      });
  }, []);

  const t = (key: string) => map[key] || DEFAULTS[key] || "";
  const faqs = parseFaqItems(map["contact.faq.items"]);

  return (
    <>
      <div className="page-hero">
        <div className="section-tag">Contact Us</div>
        <h1>{t("contact.hero.title")}</h1>
        <p>{t("contact.hero.subtitle")}</p>
      </div>

      <div style={{ background: "var(--bg-primary)" }}>
        <div className="contact-layout">
          <div>
            <ContactForm />
          </div>

          <div className="contact-sidebar">
            <div className="info-card">
              <div className="info-card-title">📍 Contact Details</div>

              <div className="contact-item">
                <div className="ci-icon">🏛️</div>
                <div>
                  <div className="ci-label">Address</div>
                  <div className="ci-value">{t("contact.info.address")}</div>
                </div>
              </div>

              <div className="contact-item">
                <div className="ci-icon">📧</div>
                <div>
                  <div className="ci-label">General Enquiries</div>
                  <div className="ci-value">
                    <a href={`mailto:${t("contact.info.email")}`}>{t("contact.info.email")}</a>
                  </div>
                </div>
              </div>

              <div className="contact-item" style={{ marginBottom: 0 }}>
                <div className="ci-icon">📞</div>
                <div>
                  <div className="ci-label">Phone</div>
                  <div className="ci-value">{t("contact.info.phone")}</div>
                </div>
              </div>
            </div>

            <div className="info-card">
              <div className="info-card-title">🕐 Office Hours</div>
              <table className="hours-table">
                <tbody>
                  <tr>
                    <td>Sunday – Thursday</td>
                    <td className="hours-open">9:00 AM – 5:00 PM</td>
                  </tr>
                  <tr>
                    <td>Friday</td>
                    <td className="hours-closed">Closed</td>
                  </tr>
                  <tr>
                    <td>Saturday</td>
                    <td className="hours-closed">Closed</td>
                  </tr>
                  <tr>
                    <td>Public Holidays</td>
                    <td className="hours-closed">Closed</td>
                  </tr>
                </tbody>
              </table>
              <p
                style={{
                  fontSize: "0.75rem",
                  color: "var(--text-muted)",
                  marginTop: "0.8rem",
                  lineHeight: 1.55,
                }}
              >
                {t("contact.info.hours")}. Response to email enquiries within 2 business days.
              </p>
            </div>

            <div className="info-card">
              <div className="info-card-title">🔗 Find Us Online</div>
              <div className="social-row">
                <a className="social-btn" href="#" target="_blank" rel="noreferrer">
                  𝕏 Twitter
                </a>
                <a className="social-btn" href="#" target="_blank" rel="noreferrer">
                  in LinkedIn
                </a>
                <a className="social-btn" href="#" target="_blank" rel="noreferrer">
                  ▶ YouTube
                </a>
                <a className="social-btn" href="#" target="_blank" rel="noreferrer">
                  📘 Facebook
                </a>
              </div>
              <p
                style={{
                  fontSize: "0.75rem",
                  color: "var(--text-muted)",
                  marginTop: "0.9rem",
                  lineHeight: 1.55,
                }}
              >
                Follow us for station data updates, new dataset announcements, and
                research highlights.
              </p>
            </div>
          </div>
        </div>
      </div>

      <section style={{ background: "var(--bg-secondary)" }}>
        <div className="section-tag">Departments</div>
        <h2 className="section-title">Contact the Right Team</h2>
        <p className="section-sub">
          Skip the queue by emailing the department most relevant to your query.
        </p>
        <div className="dept-grid">
          {DEPARTMENTS.map((d) => (
            <div className="dept-card" key={d.name}>
              <div className="dept-header">
                <div className="dept-icon">{d.icon}</div>
                <div className="dept-name">{d.name}</div>
              </div>
              <div className="dept-email">
                <a href={`mailto:${d.email}`}>{d.email}</a>
              </div>
              <div className="dept-rt">
                Typical response: <b>{d.rt}</b>
              </div>
              <p
                style={{
                  fontSize: "0.78rem",
                  color: "var(--text-muted)",
                  marginTop: "0.6rem",
                  lineHeight: 1.55,
                }}
              >
                {d.desc}
              </p>
            </div>
          ))}
        </div>
      </section>

      {faqs.length > 0 && (
        <div className="faq-section">
          <div className="section-tag">FAQ</div>
          <h2 className="section-title">Frequently Asked Questions</h2>
          <p className="section-sub">
            Quick answers to the most common questions we receive.
          </p>
          <div className="faq-grid">
            {faqs.map((f) => (
              <FaqItem key={f.q} q={f.q} a={f.a} />
            ))}
          </div>
        </div>
      )}

      <div className="map-embed-section">
        <div className="section-tag">Location</div>
        <h2 className="section-title">Find Our Office</h2>
        <p className="section-sub">
          University of Dhaka, Dhaka 1000, Bangladesh.
        </p>
        <div className="map-embed-frame">
          <iframe
            title="University of Dhaka location on Google Maps"
            src="https://maps.google.com/maps?q=University+of+Dhaka&t=&z=15&ie=UTF8&iwloc=&output=embed"
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
            allowFullScreen
          />
        </div>
        <a
          className="map-embed-link"
          href="https://www.google.com/maps/search/?api=1&query=University+of+Dhaka"
          target="_blank"
          rel="noreferrer"
        >
          Open in Google Maps →
        </a>
      </div>
    </>
  );
}
