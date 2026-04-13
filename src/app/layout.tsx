import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar/Sidebar";
import AuthProvider from "@/components/AuthProvider";
import { SpaceProvider } from "@/contexts/SpaceContext";
import MobileShell from "@/components/MobileShell";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Second Brain",
  description: "A knowledge base powered by LLM ingestion and graph linking",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={inter.className}>
        <AuthProvider>
          <SpaceProvider>
            <MobileShell sidebar={<Sidebar />}>
              {children}
            </MobileShell>
          </SpaceProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
