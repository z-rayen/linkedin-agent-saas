import type { ReactNode } from "react";

export function EmptyState({
  children,
  action,
}: {
  readonly children: ReactNode;
  readonly action?: ReactNode;
}) {
  return (
    <div className="empty">
      <p>{children}</p>
      {action}
    </div>
  );
}
