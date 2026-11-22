import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n/I18nProvider";
import { ErrorBoundary } from "./ErrorBoundary";
import { LocaleSwitch } from "./LocaleSwitch";
import styles from "./Layout.module.css";

/** The application frame. A route error is contained inside the page, so the navigation stays usable. */
export function Layout() {
  const { t } = useI18n();
  const { signOut } = useAuth();
  const location = useLocation();

  return (
    <div className={styles.shell}>
      <a className="skip-link" href="#main-content">
        {t("app.skip")}
      </a>
      <header className={styles.header}>
        <div className={styles.bar}>
          <span className={styles.brand}>{t("app.name")}</span>
          <nav className={styles.nav} aria-label={t("app.navLabel")}>
            <NavLink to="/applications" end>
              {t("nav.applications")}
            </NavLink>
            <NavLink to="/applications/new">{t("nav.newApplication")}</NavLink>
            <NavLink to="/master-cv">{t("nav.masterCv")}</NavLink>
            <NavLink to="/research">{t("nav.research")}</NavLink>
            <NavLink to="/watch">{t("nav.watch")}</NavLink>
            <NavLink to="/content">{t("nav.content")}</NavLink>
            <NavLink to="/profile">{t("nav.profile")}</NavLink>
            <NavLink to="/connections">{t("nav.connections")}</NavLink>
            <NavLink to="/account">{t("nav.account")}</NavLink>
          </nav>
          <div className={styles.tools}>
            <LocaleSwitch />
            <button
              type="button"
              className="button"
              onClick={() => {
                signOut("logout");
              }}
            >
              {t("nav.logout")}
            </button>
          </div>
        </div>
      </header>
      <main id="main-content" tabIndex={-1} className={styles.main}>
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
