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
    const serverMessage =
      typeof data.message === "string" ? data.message.trim() : "";

    const looksTechnical =
      !serverMessage ||
      serverMessage.length > 500 ||
      /<[^>]*>|traceback|sqlalchemy|psycopg|database error|internal server error/i.test(serverMessage);

    const fallbackMessages = {
      400: "Please check the information you entered and try again.",
      401: "Your session has expired. Please sign in again.",
      403: "You do not have permission to complete this action.",
      404: "The requested information or action could not be found.",
      409: "This action conflicts with the current account or record state. Refresh and try again.",
      413: "The submitted information is too large.",
      422: "Some of the information provided is invalid. Please review it and try again.",
      429: "Too many requests. Please wait a moment and try again.",
      500: "Something went wrong on our side. Please try again.",
      502: "The service is temporarily unavailable. Please try again shortly.",
      503: "The service is temporarily unavailable. Please try again shortly.",
      504: "The request took too long. Please try again.",
    };

    const error = new Error(
      looksTechnical
        ? fallbackMessages[response.status] ||
          "The request could not be completed. Please try again."
        : serverMessage
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

  listCurrencies() {
    return apiRequest("/auth/currencies");
  },

  updateProfile(payload) {
    return apiRequest("/auth/profile", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  changePassword(payload) {
    return apiRequest("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listSessions() {
    return apiRequest("/auth/sessions");
  },

  revokeSession(sessionId) {
    return apiRequest(`/auth/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  },

  revokeOtherSessions() {
    return apiRequest("/auth/sessions/revoke-others", {
      method: "POST",
    });
  },

  accountDeletionEligibility() {
    return apiRequest("/auth/account-deletion/eligibility");
  },

  deleteAccount(payload) {
    return apiRequest("/auth/account-deletion", {
      method: "DELETE",
      body: JSON.stringify(payload),
    });
  },

  createBusinessWorkspace(payload) {
    return apiRequest("/auth/workspaces/business", {
      method: "POST",
      body: JSON.stringify(payload),
    });
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


export const platformLendingApi = {
  listProducts() {
    return apiRequest("/platform/products");
  },
  listReceivingAccounts(productId) {
    return apiRequest(`/platform/receiving-accounts/${encodeURIComponent(productId)}`);
  },

  listLoans(options = {}) {
    const params = new URLSearchParams();
    if (options.page) params.set("page", String(options.page));
    if (options.per_page) params.set("per_page", String(options.per_page));
    const query = params.toString();
    return apiRequest(`/platform/loans${query ? `?${query}` : ""}`);
  },

  getLoan(loanId) {
    return apiRequest(`/platform/loans/${encodeURIComponent(loanId)}`);
  },

  listApplications() {
    return apiRequest("/platform/applications");
  },

  createApplication(payload) {
    return apiRequest("/platform/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  withdrawApplication(applicationId) {
    return apiRequest(
      `/platform/applications/${encodeURIComponent(applicationId)}`,
      { method: "DELETE" }
    );
  },

  submitRepayment(loanId, payload) {
    return apiRequest(
      `/platform/loans/${encodeURIComponent(loanId)}/payments`,
      {
        method: "POST",
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


export const platformOwnerApi = {
  checkAccess() {
    return apiRequest("/platform/owner/access");
  },

  getPortfolioReport() {
    return apiRequest("/platform/owner/reports/portfolio");
  },

  getAccountingSettings() {
    return apiRequest("/platform/owner/accounting-settings");
  },

  updateAccountingSettings(payload) {
    return apiRequest("/platform/owner/accounting-settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  listFinancialAccounts(currencyCode = "") {
    const params = new URLSearchParams();
    if (currencyCode) params.set("currency_code", currencyCode);
    const query = params.toString();
    return apiRequest(
      `/platform/owner/financial-accounts${query ? `?${query}` : ""}`
    );
  },

  createFinancialAccount(payload) {
    return apiRequest("/platform/owner/financial-accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listCustomerAccounts(loanId) {
    return apiRequest(
      `/platform/owner/loans/${encodeURIComponent(loanId)}/customer-accounts`
    );
  },

  listCollections(options = {}) {
    const params = new URLSearchParams();
    for (const key of ["currency_code", "page", "per_page"]) {
      if (options[key] !== undefined && options[key] !== "") {
        params.set(key, String(options[key]));
      }
    }
    const query = params.toString();
    return apiRequest(`/platform/owner/collections${query ? `?${query}` : ""}`);
  },

  listApplications(options = {}) {
    const params = new URLSearchParams();
    for (const key of ["status", "page", "per_page"]) {
      if (options[key] !== undefined && options[key] !== "") {
        params.set(key, String(options[key]));
      }
    }
    const query = params.toString();
    return apiRequest(`/platform/owner/applications${query ? `?${query}` : ""}`);
  },

  decideApplication(applicationId, payload) {
    return apiRequest(
      `/platform/owner/applications/${encodeURIComponent(applicationId)}/decision`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  createLoanFromApplication(applicationId) {
    return apiRequest(
      `/platform/owner/applications/${encodeURIComponent(applicationId)}/create-loan`,
      { method: "POST", body: JSON.stringify({}) }
    );
  },

  listProducts(options = {}) {
    const params = new URLSearchParams();
    for (const key of ["is_active", "page", "per_page"]) {
      if (options[key] !== undefined && options[key] !== "") {
        params.set(key, String(options[key]));
      }
    }
    const query = params.toString();
    return apiRequest(`/platform/owner/products${query ? `?${query}` : ""}`);
  },

  createProduct(payload) {
    return apiRequest("/platform/owner/products", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateProduct(productId, payload) {
    return apiRequest(`/platform/owner/products/${encodeURIComponent(productId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  listLoans(options = {}) {
    const params = new URLSearchParams();
    for (const key of ["status", "page", "per_page"]) {
      if (options[key] !== undefined && options[key] !== "") {
        params.set(key, String(options[key]));
      }
    }
    const query = params.toString();
    return apiRequest(`/platform/owner/loans${query ? `?${query}` : ""}`);
  },

  getLoan(loanId) {
    return apiRequest(`/platform/owner/loans/${encodeURIComponent(loanId)}`);
  },

  disburseLoan(loanId, payload) {
    return apiRequest(
      `/platform/owner/loans/${encodeURIComponent(loanId)}/disburse`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  confirmPayment(paymentId, payload) {
    return apiRequest(
      `/platform/owner/collections/payments/${encodeURIComponent(paymentId)}/confirm`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  rejectPayment(paymentId, payload) {
    return apiRequest(
      `/platform/owner/collections/payments/${encodeURIComponent(paymentId)}/reject`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  reversePayment(paymentId, payload) {
    return apiRequest(
      `/platform/owner/collections/payments/${encodeURIComponent(paymentId)}/reverse`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  listInvestors(options = {}) {
    const params = new URLSearchParams();
    for (const key of ["search", "is_active", "page", "per_page"]) {
      if (options[key] !== undefined && options[key] !== "") {
        params.set(key, String(options[key]));
      }
    }
    const query = params.toString();
    return apiRequest(`/platform/owner/investors${query ? `?${query}` : ""}`);
  },

  createInvestor(payload) {
    return apiRequest("/platform/owner/investors", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getInvestor(investorId) {
    return apiRequest(`/platform/owner/investors/${encodeURIComponent(investorId)}`);
  },

  recordContribution(investorId, payload) {
    return apiRequest(
      `/platform/owner/investors/${encodeURIComponent(investorId)}/contributions`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  recordWithdrawal(investorId, payload) {
    return apiRequest(
      `/platform/owner/investors/${encodeURIComponent(investorId)}/withdrawals`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  reverseContribution(contributionId, payload) {
    return apiRequest(
      `/platform/owner/investors/contributions/${encodeURIComponent(contributionId)}/reverse`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },

  reverseWithdrawal(withdrawalId, payload) {
    return apiRequest(
      `/platform/owner/investors/withdrawals/${encodeURIComponent(withdrawalId)}/reverse`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
};
