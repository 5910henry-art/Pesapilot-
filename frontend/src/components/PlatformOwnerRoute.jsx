import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { apiRequest } from "../api/client";

export default function PlatformOwnerRoute() {
  const { loading, isAuthenticated } = useAuth();
  const [checking, setChecking] = useState(true);
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    let active = true;

    if (loading || !isAuthenticated) {
      setChecking(false);
      return;
    }

    setChecking(true);

    apiRequest("/platform/owner/access")
      .then((data) => {
        if (active) {
          setAllowed(data.success === true && data.role === "owner");
        }
      })
      .catch(() => {
        if (active) setAllowed(false);
      })
      .finally(() => {
        if (active) setChecking(false);
      });

    return () => {
      active = false;
    };
  }, [loading, isAuthenticated]);

  if (loading || checking) {
    return <div className="auth-loading-skeleton">Verifying platform owner access...</div>;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!allowed) {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
