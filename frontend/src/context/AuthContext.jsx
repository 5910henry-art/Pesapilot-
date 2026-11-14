import {
  useCallback,
  useEffect,
  useState,
} from "react";

import {
  authApi,
  saveTokens,
  clearTokens,
  getRefreshToken,
  refreshSession,
} from "../api/client";

import { AuthContext } from "./auth-context";

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);

  const applyUserData = useCallback((data) => {
    setUser(data.user);
    setProfile(data.profile);
    setWorkspaces(data.workspaces || []);
  }, []);

  const loadCurrentUser = useCallback(async () => {
    const data = await authApi.me();
    applyUserData(data);
    return data;
  }, [applyUserData]);

  useEffect(() => {
    let mounted = true;

    async function restoreSession() {
      try {
        if (!getRefreshToken()) {
          clearTokens();
          return;
        }

        await refreshSession();
        const data = await authApi.me();

        if (mounted) {
          applyUserData(data);
        }
      } catch {
        clearTokens();

        if (mounted) {
          setUser(null);
          setProfile(null);
          setWorkspaces([]);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }

    restoreSession();

    return () => {
      mounted = false;
    };
  }, [applyUserData]);

  const login = useCallback(async (credentials) => {
    const data = await authApi.login(credentials);

    saveTokens(data.access_token, data.refresh_token);

    try {
      await loadCurrentUser();
    } catch (error) {
      clearTokens();
      throw error;
    }
  }, [loadCurrentUser]);

  const register = useCallback((details) => {
    return authApi.register(details);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      setProfile(null);
      setWorkspaces([]);
    }
  }, []);

  const value = {
    user,
    profile,
    workspaces,
    activeWorkspace: workspaces[0] || null,
    loading,
    isAuthenticated: Boolean(user),
    login,
    register,
    logout,
    reloadUser: loadCurrentUser,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}
