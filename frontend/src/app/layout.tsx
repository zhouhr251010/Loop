import type { Metadata, Viewport } from "next";
import { AppProviders } from "@/components/AppProviders";
import { NavBar } from "@/components/NavBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Loop Research Platform",
  description: "Laboratory UI for Loop parallel-society experiments",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  userScalable: true,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AppProviders>
          <NavBar />
          {children}
        </AppProviders>
      </body>
    </html>
  );
}
