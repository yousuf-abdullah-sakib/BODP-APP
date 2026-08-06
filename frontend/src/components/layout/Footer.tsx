import Link from "next/link";

export default function Footer() {
  return (
    <footer>
      <div className="footer-grid">
        <div>
          <div className="footer-brand">BODP</div>
          <div className="footer-desc">
            Bangladesh Oceanographic Data Portal — Open access marine &amp; environmental data for
            the Bay of Bengal region.
          </div>
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
              <Link href="#">Data License (CC BY 4.0)</Link>
            </li>
            <li>
              <Link href="#">Privacy Policy</Link>
            </li>
            <li>
              <Link href="#">Terms of Use</Link>
            </li>
          </ul>
        </div>
      </div>
      <div className="footer-bottom">
        <span>© 2024 Bangladesh Oceanographic Data Portal · CC BY 4.0</span>
        <span>Dhaka, Bangladesh 🇧🇩</span>
      </div>
    </footer>
  );
}
