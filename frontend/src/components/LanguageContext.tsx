"use client";

import {
  ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Dictionary, Language, dictionary } from "@/locales/dictionary";

const UI_LANGUAGE_STORAGE_KEY = "loop_ui_language";

type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Dictionary;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

function isLanguage(value: unknown): value is Language {
  return value === "zh" || value === "en";
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>("zh");

  useEffect(() => {
    try {
      const storedLanguage = window.localStorage.getItem(UI_LANGUAGE_STORAGE_KEY);
      if (isLanguage(storedLanguage)) {
        setLanguageState(storedLanguage);
      }
    } catch {
      setLanguageState("zh");
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);

  function setLanguage(nextLanguage: Language) {
    setLanguageState(nextLanguage);
    try {
      window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, nextLanguage);
    } catch {
      // The in-memory language still updates if localStorage is unavailable.
    }
  }

  const value = useMemo(
    () => ({
      language,
      setLanguage,
      t: dictionary[language],
    }),
    [language],
  );

  return (
    <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useLanguage must be used inside LanguageProvider");
  }
  return context;
}
