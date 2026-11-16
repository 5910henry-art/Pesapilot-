import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock3, ShieldCheck, LogOut } from "lucide-react";
import {
  getAccessToken,
  refreshSession,
} from "../api/client";
import { useAuth } from "../context/useAuth";

function getTokenExpiry(token) {
  try {
    const payload = token.split(".")[1];
    const normalized = payload
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const decoded = atob(normalized);
    return JSON.parse(decoded).exp * 1000;
  } catch {
    return null;
  }
}

export default function SessionExpiryGuard() {
  const { isAuthenticated, logout } = useAuth();
  const navigate = useNavigate();

  const [now, setNow] = useState(0);
  const [accessToken, setAccessToken] = useState(getAccessToken);
  const [refreshing, setRefreshing] = useState(false);
  const handledExpiry = useRef(false);

  useEffect(() => {
    const syncToken = () => setAccessToken(getAccessToken());
    window.addEventListener("pesapilot:tokens-updated", syncToken);
    return () => window.removeEventListener("pesapilot:tokens-updated", syncToken);
  }, []);

  const expiresAt = useMemo(
    () => getTokenExpiry(accessToken),
    [accessToken]
  );

  const secondsRemaining = expiresAt
    ? Math.max(0, Math.ceil((expiresAt - now) / 1000))
    : null;

  useEffect(() => {
    const updateTime = () => setNow(Date.now());
    updateTime();

    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!isAuthenticated || !accessToken) return;

    if (secondsRemaining > 0) {
      handledExpiry.current = false;
      return;
    }

    if (secondsRemaining === 0 && !handledExpiry.current) {
      handledExpiry.current = true;

      logout().finally(() => {
        navigate("/login", { replace: true });
      });
    }
  }, [
    isAuthenticated,
    accessToken,
    secondsRemaining,
    logout,
    navigate,
  ]);

  async function handleContinue() {
    if (refreshing) return;

    setRefreshing(true);

    try {
      await refreshSession();
      setNow(Date.now());
    } catch {
      await logout();
      navigate("/login", { replace: true });
    } finally {
      setRefreshing(false);
    }
  }

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  if (
    !isAuthenticated ||
    !expiresAt ||
    secondsRemaining === null ||
    secondsRemaining <= 0 ||
    secondsRemaining > 120
  ) {
    return null;
  }

  const minutes = Math.floor(secondsRemaining / 60);
  const seconds = secondsRemaining % 60;

  return (
    <div className="session-warning-overlay">
      <section
        className="session-warning-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-warning-title"
      >
        <div className="session-warning-icon">
          <Clock3 size={25} />
        </div>

        <p className="session-warning-eyebrow">
          SECURITY NOTICE
        </p>

        <h2 id="session-warning-title">
          Your session is about to expire
        </h2>

        <p className="session-warning-description">
          For your security, you will be signed out unless
          you choose to continue your session.
        </p>

        <div className="session-countdown">
          <strong>
            {String(minutes).padStart(2, "0")}:
            {String(seconds).padStart(2, "0")}
          </strong>
          <span>Time remaining</span>
        </div>

        <button
          type="button"
          className="session-continue-button"
          onClick={handleContinue}
          disabled={refreshing}
        >
          <ShieldCheck size={17} />
          {refreshing ? "Extending session..." : "Stay signed in"}
        </button>

        <button
          type="button"
          className="session-logout-button"
          onClick={handleLogout}
          disabled={refreshing}
        >
          <LogOut size={16} />
          Log out
        </button>
      </section>
    </div>
  );
}
