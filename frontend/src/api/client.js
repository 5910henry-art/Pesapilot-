const API_BASE = "/api/v1";

const getStored = (key) => {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
};

const saveStored = (key, value) => {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    // Storage may be unavailable.
  }
};

export function getAccessToken() {
  return getStored("pesapilot_access_token");
}

export function getRefreshToken() {
  return getStored("pesapilot_refresh_token");
}

export function saveTokens(accessToken, refreshToken) {
  saveStored("pesapilot_access_token", accessToken);
  saveStored("pesapilot_refresh_token", refreshToken);
}

export function clearTokens() {
  saveTokens(null, null);
}

async function readResponse(response) {
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = new Error(
      data.message || `Request failed (${response.status})`
    );
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

let refreshPromise = null;

export async function refreshSession() {
  const refreshToken = getRefreshToken();

  if (!refreshToken) {
    clearTokens();
    throw new Error("Your session has expired. Please sign in again.");
  }

  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${refreshToken}`,
          },
        });

        const data = await readResponse(response);

        saveTokens(data.access_token, data.refresh_token);

        return data.access_token;
      } catch (error) {
        clearTokens();
        throw error;
      } finally {
        refreshPromise = null;
      }
    })();
  }

  return refreshPromise;
}

export async function apiRequest(path, options = {}) {
  const {
    skipRefresh = false,
    headers: customHeaders = {},
    ...requestOptions
  } = options;

  const headers = {
    ...customHeaders,
  };

  if (requestOptions.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const accessToken = getAccessToken();

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  let response = await fetch(`${API_BASE}${path}`, {
    ...requestOptions,
    headers,
  });

  if (
    response.status === 401 &&
    !skipRefresh &&
    ![
      "/auth/login",
      "/auth/register",
      "/auth/refresh",
      "/auth/logout",
    ].includes(path)
  ) {
    await refreshSession();

    const newHeaders = {
      ...headers,
      Authorization: `Bearer ${getAccessToken()}`,
    };

    response = await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: newHeaders,
    });
  }

  return readResponse(response);
}

export const authApi = {
  register(payload) {
    return apiRequest("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
      skipRefresh: true,
    });
  },

  login(payload) {
    return apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
      skipRefresh: true,
    });
  },

  me() {
    return apiRequest("/auth/me");
  },

  async logout() {
    const refreshToken = getRefreshToken();

    try {
      if (refreshToken) {
        await fetch(`${API_BASE}/auth/logout`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${refreshToken}`,
          },
        });
      }
    } finally {
      clearTokens();
    }
  },
};

export const financeApi = {
  getDashboardSummary(workspaceId) {
    return apiRequest(
      `/finance/dashboard?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  listAccounts(workspaceId) {
    return apiRequest(
      `/finance/accounts?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  listExpenseCategories(workspaceId) {
    return apiRequest(
      `/budgets/categories?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createExpenseCategory(payload) {
    return apiRequest("/budgets/categories", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listBudgets(workspaceId) {
    return apiRequest(
      `/budgets?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createBudget(payload) {
    return apiRequest("/budgets", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getBalances(workspaceId) {
    return apiRequest(
      `/finance/accounts/balances?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createAccount(payload) {
    return apiRequest("/finance/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listEntries(workspaceId, options = {}) {
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      limit: String(options.limit || 50),
    });

    if (options.status) params.set("status", options.status);

    return apiRequest(`/finance/entries?${params.toString()}`);
  },

  createEntry(payload) {
    return apiRequest("/finance/entries", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  postEntry(entryId, workspaceId) {
    return apiRequest(
      `/finance/entries/${encodeURIComponent(entryId)}/post?workspace_id=${encodeURIComponent(workspaceId)}`,
      { method: "POST" }
    );
  },

  reverseEntry(entryId, workspaceId) {
    return apiRequest(
      `/finance/entries/${encodeURIComponent(entryId)}/reverse`,
      {
        method: "POST",
        body: JSON.stringify({
          workspace_id: workspaceId,
          reference: `REV-${entryId}-${Date.now()}`,
          description: "Reversal of original transaction",
        }),
      }
    );
  },
};
