import { useI18n, type Locale } from "../i18n/I18nProvider";
import styles from "./LocaleSwitch.module.css";

/** Each language is named in its own language, so a user can find theirs whatever the current one. */
const LOCALE_NAMES: Readonly<Record<Locale, string>> = { fr: "Français", en: "English" };
const LOCALES: readonly Locale[] = ["fr", "en"];

/** The language choice, always visible. Two buttons with aria-pressed: the state is announced, not only coloured. */
export function LocaleSwitch() {
  const { t, locale, setLocale } = useI18n();
  return (
    <div role="group" aria-label={t("app.languageLabel")} className={styles.group}>
      {LOCALES.map((code) => (
        <button
          key={code}
          type="button"
          lang={code}
          aria-pressed={code === locale}
          className={code === locale ? styles.active : styles.option}
          onClick={() => {
            setLocale(code);
          }}
        >
          {LOCALE_NAMES[code]}
        </button>
      ))}
    </div>
  );
}
