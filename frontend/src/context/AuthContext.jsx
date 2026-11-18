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
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(null);
  const [loading, setLoading] = useState(true);

  const applyUserData = useCallback((data) => {
    setUser(data.user);
    setProfile(data.profile);
    const availableWorkspaces = data.workspaces || [];
    setWorkspaces(availableWorkspaces);

    const storageKey = `pesapilot.activeWorkspace.${data.user.id}`;
    const savedWorkspaceId = localStorage.getItem(storageKey);
    const selectedWorkspace = availableWorkspaces.find(
      (workspace) => workspace.id === savedWorkspaceId
    );

    const nextWorkspace = selectedWorkspace || availableWorkspaces[0] || null;
    setSelectedWorkspaceId(nextWorkspace?.id || null);

    if (nextWorkspace) {
      localStorage.setItem(storageKey, nextWorkspace.id);
    } else {
      localStorage.removeItem(storageKey);
    }
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
          setSelectedWorkspaceId(null);
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
      setSelectedWorkspaceId(null);
    }
  }, []);

  const activeWorkspace =
    workspaces.find((workspace) => workspace.id === selectedWorkspaceId) ||
    workspaces[0] ||
    null;

  const setActiveWorkspace = useCallback((workspaceId) => {
    const selected = workspaces.find(
      (workspace) => workspace.id === workspaceId
    );

    if (!selected) return;

    setSelectedWorkspaceId(selected.id);

    if (user?.id) {
      localStorage.setItem(
        `pesapilot.activeWorkspace.${user.id}`,
        selected.id
      );
    }
  }, [workspaces, user?.id]);

  const value = {
    user,
    profile,
    workspaces,
    activeWorkspace,
    setActiveWorkspace,
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
