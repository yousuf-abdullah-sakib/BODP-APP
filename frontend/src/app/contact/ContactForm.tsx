"use client";

import { useState } from "react";
import { submitContactForm } from "@/lib/api/content";
import { ApiError } from "@/lib/api/client";

const SUBJECTS = [
  { value: "data_request", label: "Dataset Request Help" },
  { value: "technical", label: "Technical Support" },
  { value: "partnership", label: "Partnership & Collaboration" },
  { value: "data_quality", label: "Data Quality Issue" },
  { value: "api", label: "API & Integration" },
  { value: "media", label: "Media & Press" },
  { value: "general", label: "General Enquiry" },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function ContactForm() {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [org, setOrg] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [consent, setConsent] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function sendMessage() {
    setError("");
    setSuccess("");

    if (!firstName.trim() || !lastName.trim()) {
      setError("Please enter your full name.");
      return;
    }
    if (!email.trim() || !EMAIL_RE.test(email.trim())) {
      setError("Please enter a valid email address.");
      return;
    }
    if (!subject) {
      setError("Please select a subject.");
      return;
    }
    if (!message.trim() || message.trim().length < 20) {
      setError("Please write a message of at least 20 characters.");
      return;
    }
    if (!consent) {
      setError("Please agree to the privacy policy to send your message.");
      return;
    }

    setSending(true);
    try {
      await submitContactForm({
        name: `${firstName.trim()} ${lastName.trim()}`,
        email: email.trim(),
        organization: org.trim() || null,
        subject,
        message: message.trim(),
      });
      setSuccess(
        `✅ Thank you, ${firstName}! Your message has been sent. We'll respond to ${email} within 2 business days.`
      );
      setFirstName("");
      setLastName("");
      setEmail("");
      setOrg("");
      setSubject("");
      setMessage("");
      setConsent(false);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to send your message. Please try again."
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="contact-form-box">
      <div className="cf-title">Send us a Message</div>
      <div className="cf-sub">
        Fill in the form and the right team member will get back to you. Fields
        marked <span style={{ color: "var(--red)" }}>*</span> are required.
      </div>

      {error && <div className="form-alert form-alert-error">{error}</div>}
      {success && <div className="form-alert form-alert-success">{success}</div>}

      <div className="form-row">
        <div className="form-group">
          <label className="form-label">
            First Name <span>*</span>
          </label>
          <input
            className="form-input"
            type="text"
            placeholder="Rahim"
            maxLength={60}
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label className="form-label">
            Last Name <span>*</span>
          </label>
          <input
            className="form-input"
            type="text"
            placeholder="Uddin"
            maxLength={60}
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
          />
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">
          Email Address <span>*</span>
        </label>
        <input
          className="form-input"
          type="email"
          placeholder="you@institution.edu"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label className="form-label">Institution / Organization</label>
        <input
          className="form-input"
          type="text"
          placeholder="BUET, CUET, NGO name…"
          maxLength={120}
          value={org}
          onChange={(e) => setOrg(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label className="form-label">
          Subject / Department <span>*</span>
        </label>
        <select
          className="form-select"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        >
          <option value="">— Select a topic —</option>
          {SUBJECTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="form-group">
        <label className="form-label">
          Message <span>*</span>
        </label>
        <textarea
          className="form-textarea"
          placeholder="Describe your question or request in as much detail as possible…"
          maxLength={2000}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
        <div className="char-count">{message.length} / 2000 characters</div>
      </div>

      <div className="form-consent">
        <input
          type="checkbox"
          id="cfConsent"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
        />
        <label htmlFor="cfConsent">
          I agree to BODP processing my contact details to respond to this enquiry.
          My data will be handled in accordance with the{" "}
          <a href="/legal/privacy-policy">Privacy Policy</a>.
        </label>
      </div>

      <button className="btn-send" disabled={sending} onClick={sendMessage}>
        <span>{sending ? "Sending…" : "Send Message"}</span>{" "}
        <span>{sending ? "⏳" : "→"}</span>
      </button>
    </div>
  );
}
