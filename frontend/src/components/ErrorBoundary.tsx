import { Component, type ErrorInfo, type ReactNode } from "react";
import { useI18n } from "../i18n/I18nProvider";
import styles from "./ErrorBoundary.module.css";

interface Props {
  readonly children: ReactNode;
}
interface State {
  readonly failed: boolean;
}

/** Catches a rendering error in one part of the page, so the rest of the application stays usable. The error is
 * logged without any payload, never the content it was rendering. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("render failed", {
      name: error.name,
      component: info.componentStack ? "yes" : "no",
    });
  }

  override render(): ReactNode {
    if (this.state.failed) {
      return (
        <ErrorFallback
          onRetry={() => {
            this.setState({ failed: false });
          }}
        />
      );
    }
    return this.props.children;
  }
}

function ErrorFallback({ onRetry }: { readonly onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <section role="alert" className={styles.box}>
      <h2>{t("common.errorBoundaryTitle")}</h2>
      <p>{t("common.errorBoundaryBody")}</p>
      <button
        type="button"
        className={styles.button}
        onClick={() => {
          onRetry();
        }}
      >
        {t("common.retry")}
      </button>
    </section>
  );
}
