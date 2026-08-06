import type { Metadata } from "next";
import "./blog.css";
import BlogClient from "./BlogClient";

export const metadata: Metadata = {
  title: "Blog — BODP",
};

export default function BlogPage() {
  return <BlogClient />;
}
