"use client";

import { ReactNode } from "react";
import { LanguageProvider } from "@/components/LanguageContext";

export function AppProviders({ children }: { children: ReactNode }) {
  return <LanguageProvider>{children}</LanguageProvider>;
}
