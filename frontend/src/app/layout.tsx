import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Loop Research Platform",
  description: "Laboratory UI for Loop parallel-society experiments",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
