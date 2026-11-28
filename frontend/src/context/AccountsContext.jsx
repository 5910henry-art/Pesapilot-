import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { financeApi } from "../api/client";
import { useAuth } from "./useAuth";

const AccountsContext = createContext(null);

export function AccountsProvider({ children }) {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;

  const [data, setData] = useState({
    workspaceId: null,
    accounts: [],
    balances: [],
    loading: true,
    error: "",
  });

  const cacheRef = useRef(new Map());
  const requestsRef = useRef(new Map());
  const cacheGenerationRef = useRef(0);
  const currentWorkspaceRef = useRef(workspaceId);

  currentWorkspaceRef.current = workspaceId;

  const loadAccounts = useCallback(async (force = false) => {
    if (!workspaceId) {
      setData({
        workspaceId: null,
        accounts: [],
        balances: [],
        loading: false,
        error: "",
      });
      return;
    }

    const cached = cacheRef.current.get(workspaceId);

    if (!force && cached) {
      setData({
        workspaceId,
        ...cached,
        loading: false,
        error: "",
      });
      return;
    }

    if (!force && requestsRef.current.has(workspaceId)) {
      return requestsRef.current.get(workspaceId);
    }

    setData((previous) => ({
      workspaceId,
      accounts: previous.workspaceId === workspaceId
        ? previous.accounts : [],
      balances: previous.workspaceId === workspaceId
        ? previous.balances : [],
      loading: true,
      error: "",
    }));

    const requestGeneration = cacheGenerationRef.current;
    const request = Promise.all([
      financeApi.listAccounts(workspaceId),
      financeApi.getBalances(workspaceId),
    ])
      .then(([accountData, balanceData]) => {
        const result = {
          accounts: accountData.accounts || [],
          balances: balanceData.accounts || [],
        };

        if (requestGeneration !== cacheGenerationRef.current) {
          return result;
        }

        cacheRef.current.set(workspaceId, result);

        if (currentWorkspaceRef.current === workspaceId) {
          setData({
            workspaceId,
            ...result,
            loading: false,
            error: "",
          });
        }

        return result;
      })
      .catch((error) => {
        if (
          requestGeneration === cacheGenerationRef.current &&
          currentWorkspaceRef.current === workspaceId
        ) {
          setData((previous) => ({
            ...previous,
            workspaceId,
            loading: false,
            error: error.message || "Could not load accounts.",
          }));
        }

        throw error;
      })
      .finally(() => {
        if (requestsRef.current.get(workspaceId) === request) {
          requestsRef.current.delete(workspaceId);
        }
      });

    requestsRef.current.set(workspaceId, request);

    return request;
  }, [workspaceId]);

  useEffect(() => {
    loadAccounts().catch(() => {});
  }, [loadAccounts]);

  const refreshAccounts = useCallback(() => {
    return loadAccounts(true);
  }, [loadAccounts]);

  const clearAccountsCache = useCallback(() => {
    cacheGenerationRef.current += 1;
    cacheRef.current.clear();
    requestsRef.current.clear();

    setData({
      workspaceId: workspaceId || null,
      accounts: [],
      balances: [],
      loading: Boolean(workspaceId),
      error: "",
    });

    return workspaceId ? loadAccounts(true) : Promise.resolve(null);
  }, [loadAccounts, workspaceId]);

  const currentData = data.workspaceId === workspaceId
    ? data
    : {
        accounts: [],
        balances: [],
        loading: Boolean(workspaceId),
        error: "",
      };

  const value = useMemo(() => ({
    accounts: currentData.accounts,
    balances: currentData.balances,
    loading: currentData.loading,
    error: currentData.error,
    refreshAccounts,
    clearAccountsCache,
  }), [currentData, refreshAccounts, clearAccountsCache]);

  return (
    <AccountsContext.Provider value={value}>
      {children}
    </AccountsContext.Provider>
  );
}

export function useAccounts() {
  const context = useContext(AccountsContext);

  if (!context) {
    throw new Error("useAccounts must be used inside AccountsProvider.");
  }

  return context;
}
