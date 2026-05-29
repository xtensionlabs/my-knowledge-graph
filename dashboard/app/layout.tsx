import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "@xyflow/react/dist/style.css";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Synapse",
  description: "Cognitive operating system — dashboard.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Set the address-bar tint to match the surface color so it blends seamlessly.
  themeColor: "#0a0a0c",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-bg text-fg">
        {/*
          Mobile: column — top header (rendered by Sidebar) + main content.
          Desktop: row — fixed left sidebar + flexible main area.
        */}
        <div className="flex flex-col md:flex-row min-h-screen">
          <Sidebar />
          <main className="flex-1 min-w-0 min-h-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
