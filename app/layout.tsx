import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "Yap Editor — Transcript-Based Video Editor",
  description: "Upload your video, let Gemini find the gold, export a tighter cut.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body>
        <div className="grain" />
        {children}
        <Toaster
          theme="dark"
          toastOptions={{
            style: {
              background: "#0f1011",
              border: "1px solid #1e2022",
              color: "#e8e9eb",
              fontFamily: "'DM Sans', sans-serif",
            },
          }}
        />
      </body>
    </html>
  );
}
