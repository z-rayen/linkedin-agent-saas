import type { ReactNode } from "react";

export type BannerTone = "info" | "success" | "warning" | "danger";

/** A visible message. Errors use role="alert" so screen readers announce them at once; the others are polite. */
export function Banner({
  tone,
  children,
  title,
}: {
  readonly tone: BannerTone;
  readonly children: ReactNode;
  readonly title?: string | undefined;
}) {
  const role = tone === "danger" ? "alert" : "status";
  return (
    <div role={role} className={`banner banner-${tone}`}>
      {title ? <strong className="banner-title">{title}</strong> : null}
      <div>{children}</div>
    </div>
  );
}
