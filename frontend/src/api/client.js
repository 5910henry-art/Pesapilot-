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

  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("pesapilot:tokens-updated"));
  }
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

  updateAccount(accountId, payload) {
    return apiRequest(`/finance/accounts/${encodeURIComponent(accountId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  deleteAccount(accountId, workspaceId) {
    return apiRequest(
      `/finance/accounts/${encodeURIComponent(accountId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
      { method: "DELETE" }
    );
  },

  listSavingsGoals(workspaceId) {
    return apiRequest(
      `/savings?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createSavingsGoal(payload) {
    return apiRequest("/savings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  contributeToSavings(goalId, payload) {
    return apiRequest(
      `/savings/${encodeURIComponent(goalId)}/contributions`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
  },

  withdrawFromSavings(goalId, payload) {
    return apiRequest(
      `/savings/${encodeURIComponent(goalId)}/withdrawals`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
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

  listBills(workspaceId) {
    return apiRequest(
      `/bills?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  getBill(billId, workspaceId) {
    return apiRequest(
      `/bills/${encodeURIComponent(billId)}?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createBill(payload) {
    return apiRequest("/bills", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  recordBillPayment(billId, payload) {
    return apiRequest(`/bills/${encodeURIComponent(billId)}/payments`, {
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

  listAccountTransactions(accountId, workspaceId, limit = 50) {
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      limit: String(limit),
    });

    return apiRequest(
      `/finance/accounts/${encodeURIComponent(accountId)}/transactions?${params.toString()}`
    );
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

  clearDraftEntry(entryId, workspaceId) {
    return apiRequest(
      `/finance/entries/${encodeURIComponent(entryId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
      { method: "DELETE" }
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

export const loansApi = {
  listLoans(workspaceId) {
    return apiRequest(
      `/loans?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  getLoan(loanId, workspaceId) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  createLoan(payload) {
    return apiRequest("/loans", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateLoan(loanId, payload) {
    return apiRequest(`/loans/${encodeURIComponent(loanId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  activateLoan(loanId, workspaceId) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/activate`,
      {
        method: "POST",
        body: JSON.stringify({ workspace_id: workspaceId }),
      }
    );
  },

  disburseLoan(loanId, payload) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/disburse`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
  },

  recordPayment(loanId, payload) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/payments`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
  },

  confirmPayment(loanId, paymentId, payload) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/payments/${encodeURIComponent(paymentId)}/confirm`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
  },
  updatePayment(loanId, paymentId, payload) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/payments/${encodeURIComponent(paymentId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      }
    );
  },

  deletePayment(loanId, paymentId, payload) {
    return apiRequest(
      `/loans/${encodeURIComponent(loanId)}/payments/${encodeURIComponent(paymentId)}`,
      {
        method: "DELETE",
        body: JSON.stringify(payload),
      }
    );
  },
};

export const aiApi = {
  getOverview(workspaceId) {
    return apiRequest(
      `/ai-manager/overview?workspace_id=${encodeURIComponent(workspaceId)}`
    );
  },

  ask(workspaceId, question) {
    return apiRequest("/ai-manager/ask", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: workspaceId,
        question,
        external_ai_consent: true,
      }),
    });
  },
};
