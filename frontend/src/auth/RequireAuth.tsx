import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

/** Protects the application routes. The login page remembers where the user was going, in the router state only:
 * never in the URL, so no token or path detail is copied into a link. */
export function RequireAuth() {
  const { isAuthenticated, endReason } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname, endReason }} />;
  }
  return <Outlet />;
}
