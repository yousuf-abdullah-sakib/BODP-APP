import Link from "next/link";

export default function HomePage() {
  return (
    <div className="hero">
      <div className="hero-tag">🌊 Open Ocean & Environmental Data</div>
      <h1>
        Bangladesh <em>Oceanographic</em> Data Portal
      </h1>
      <p className="hero-sub">
        Explore, visualize, and request access to marine, coastal, and environmental datasets
        from the Bay of Bengal.
      </p>
      <div className="hero-btns">
        <Link href="/catalog" className="btn-primary">
          Browse Datasets →
        </Link>
        <Link href="/login" className="btn-outline">
          Request Access
        </Link>
      </div>
    </div>
  );
}
