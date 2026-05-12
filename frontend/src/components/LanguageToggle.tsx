"use client";

import { Language } from "@/locales/dictionary";
import { useLanguage } from "@/components/LanguageContext";

type LanguageToggleProps = {
  language?: Language;
  onChange?: (language: Language) => void;
  className?: string;
};

export function LanguageToggle({
  language: controlledLanguage,
  onChange,
  className = "",
}: LanguageToggleProps) {
  const context = useLanguage();
  const language = controlledLanguage ?? context.language;
  const setLanguage = onChange ?? context.setLanguage;
  const nextLanguage = language === "zh" ? "en" : "zh";

  return (
    <button
      aria-label={context.t.common.languageToggleAria}
      className={`inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-50 ${className}`}
      onClick={() => setLanguage(nextLanguage)}
      type="button"
    >
      <span>{context.t.common.languageToggleText}</span>
    </button>
  );
}
