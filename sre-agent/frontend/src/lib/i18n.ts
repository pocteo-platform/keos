import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@/locales/en.json";
import fr from "@/locales/fr.json";
import { useApp } from "./store";

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    fr: { translation: fr },
  },
  lng: useApp.getState().locale,
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

// Mirror the Zustand locale store into i18next so any setLocale() call
// immediately swaps the active language.
useApp.subscribe((state, prev) => {
  if (state.locale !== prev.locale) {
    void i18n.changeLanguage(state.locale);
    document.documentElement.lang = state.locale;
  }
});

document.documentElement.lang = useApp.getState().locale;

export default i18n;
