import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { configureClient } from "../api/client";
import { clearToken, readToken, writeToken } from "./tokenStorage";
import { isExpired } from "./jwt";

export type SessionEndReason = "expired" | "invalid" | "logout";

interface Session {
  readonly token: string | null;
  readonly endReason: SessionEndReason | null;
}

interface AuthValue {
  readonly token: string | null;
  readonly isAuthenticated: boolean;
  readonly endReason: SessionEndReason | null;
  signIn: (token: string) => void;
  signOut: (reason?: SessionEndReason) => void;
}

const AuthContext = createContext<AuthValue | null>(null);

/** A stored token that has expired is dropped, and the login page says why. */
function initialSession(): Session {
  const token = readToken();
  if (token === null) return { token: null, endReason: null };
  if (isExpired(token)) {
    clearToken();
    return { token: null, endReason: "expired" };
  }
  return { token, endReason: null };
}

/** Holds the session and wires it into the API client. A 401 from the API ends the session. */
export function AuthProvider({ children }: { readonly children: ReactNode }) {
  const [session, setSession] = useState<Session>(initialSession);

  const signOut = useCallback((reason: SessionEndReason = "logout") => {
    clearToken();
    setSession({ token: null, endReason: reason });
  }, []);

  const signIn = useCallback((next: string) => {
    writeToken(next);
    setSession({ token: next, endReason: null });
  }, []);

  // The client is wired during render, before any child can send a request: children's effects run before parents'.
  // The handler only uses the stable state setter (through signOut), so it needs no ref.
  configureClient({
    token: () => readToken(),
    onUnauthorized: () => {
      const current = readToken();
      signOut(current !== null && isExpired(current) ? "expired" : "invalid");
    },
  });

  const value = useMemo<AuthValue>(
    () => ({
      token: session.token,
      isAuthenticated: session.token !== null,
      endReason: session.endReason,
      signIn,
      signOut,
    }),
    [session, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
