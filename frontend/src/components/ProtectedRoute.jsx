import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { Skeleton } from "./Skeleton";

export default function ProtectedRoute() {
  const { loading, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div className="auth-loading-skeleton" aria-hidden="true">
        <div className="auth-skeleton-header">
          <Skeleton width="42px" height={42} radius={12} />
          <Skeleton width="145px" height={17} />
          <Skeleton width="90px" height={12} />
        </div>

        <div className="auth-skeleton-content">
          <Skeleton width="190px" height={25} />
          <Skeleton width="260px" height={12} />
          <div className="auth-skeleton-stats">
            {[1, 2, 3].map((item) => (
              <div key={item}>
                <Skeleton width="65%" height={12} />
                <Skeleton width="85%" height={25} />
              </div>
            ))}
          </div>
          <div className="auth-skeleton-panel">
            <Skeleton width="150px" height={17} />
            {[1, 2, 3, 4].map((item) => (
              <div key={item} className="auth-skeleton-row">
                <Skeleton width={38} height={38} radius="50%" />
                <Skeleton width="55%" height={12} />
                <Skeleton width="75px" height={12} />
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return isAuthenticated
    ? <Outlet />
    : <Navigate to="/login" replace />;
}
