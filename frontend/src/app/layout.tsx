import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/context/ThemeContext";
import { ToastProvider } from "@/context/ToastContext";
import { SessionProvider } from "@/context/SessionContext";
import { ConfirmProvider } from "@/context/ConfirmContext";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";

export const metadata: Metadata = {
  title: "BODP — Bangladesh Oceanographic Data Portal",
  description:
    "A secure scientific data portal for oceanographic, coastal, pollution, and climate datasets from the Bay of Bengal.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <ThemeProvider>
          <SessionProvider>
            <ToastProvider>
              <ConfirmProvider>
                <Navbar />
                <main>{children}</main>
                <Footer />
              </ConfirmProvider>
            </ToastProvider>
          </SessionProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
