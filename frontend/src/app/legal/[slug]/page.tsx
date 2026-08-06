import "../../about/about.css";
import { getCmsBlocks } from "@/lib/api/content";

const VALID_SLUGS = [
  "terms-conditions",
  "privacy-policy",
  "download-policy",
  "citation-policy",
  "cookie-policy",
  "disclaimer",
] as const;

export default async function LegalPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;

  if (!VALID_SLUGS.includes(slug as (typeof VALID_SLUGS)[number])) {
    return (
      <div className="page-hero">
        <div className="section-tag">Legal</div>
        <h1>Page Not Found</h1>
        <p>The legal document you&apos;re looking for doesn&apos;t exist.</p>
      </div>
    );
  }

  const page = `legal-${slug}`;
  let title = "";
  let body = "";

  try {
    const blocks = await getCmsBlocks(page);
    title = blocks.find((b) => b.key === `${page}.title`)?.value ?? "";
    body = blocks.find((b) => b.key === `${page}.body`)?.value ?? "";
  } catch {
    // fall through — render the "not found" state below
  }

  if (!title && !body) {
    return (
      <div className="page-hero">
        <div className="section-tag">Legal</div>
        <h1>Page Not Found</h1>
        <p>The legal document you&apos;re looking for doesn&apos;t exist.</p>
      </div>
    );
  }

  return (
    <>
      <div className="page-hero">
        <div className="section-tag">Legal</div>
        <h1>{title}</h1>
      </div>
      <section>
        <div
          className="prose"
          style={{ maxWidth: 820, margin: "0 auto" }}
          // Body HTML is sanitized server-side at write time (see
          // backend content_service) — safe to render directly.
          dangerouslySetInnerHTML={{ __html: body }}
        />
      </section>
    </>
  );
}
