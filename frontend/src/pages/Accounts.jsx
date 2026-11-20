import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Wallet, Plus, RefreshCw, Building2, Smartphone,
  PiggyBank, TrendingUp, CreditCard, Banknote,
  X, CheckCircle2, AlertCircle, Pencil, Archive, RotateCcw, Trash2, Check, ChevronDown
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { financeApi } from "../api/client";
import TransactionEntryForm from "../components/TransactionEntryForm";
import { Skeleton, SkeletonStatCard } from "../components/Skeleton";

const CURRENCIES = [
  "KES", "USD", "EUR", "GBP", "UGX", "TZS", "RWF",
  "ZAR", "NGN", "GHS", "INR", "CAD", "AUD", "CHF",
  "JPY", "CNY", "AED", "SAR",
];

const MINOR_UNITS = {
  KES: 2, USD: 2, EUR: 2, GBP: 2, UGX: 0,
  TZS: 2, RWF: 0, ZAR: 2, NGN: 2, GHS: 2,
  INR: 2, CAD: 2, AUD: 2, CHF: 2, JPY: 0,
  CNY: 2, AED: 2, SAR: 2,
};

const ACCOUNT_TYPES = [
  { value: "cash", label: "Cash", icon: Banknote, description: "Physical money you hold and use directly.", examples: "Examples: pocket money, cash wallet, money kept at home." },
  { value: "bank", label: "Bank account", icon: Building2, description: "Money held in a bank account.", examples: "Examples: Equity Bank, KCB, Co-operative Bank, Absa." },
  { value: "mobile_money", label: "Mobile money", icon: Smartphone, description: "Money held in a mobile money wallet.", examples: "Examples: M-PESA, Airtel Money." },
  { value: "savings", label: "Savings", icon: PiggyBank, description: "Money deliberately set aside for future needs or goals.", examples: "Examples: emergency fund, school fees, house savings." },
  { value: "investment", label: "Investment", icon: TrendingUp, description: "Assets or funds held to generate returns or grow wealth.", examples: "Examples: money market funds, shares, treasury bills, bonds." },
  { value: "credit", label: "Credit / liability", icon: CreditCard, description: "A credit facility or outstanding amount that you owe.", examples: "Examples: credit card balance, bank overdraft." },
];

function formatMoney(minor, currency) {
  const unit = MINOR_UNITS[currency] ?? 2;
  const amount = Number(minor || 0) / (10 ** unit);

  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: unit,
      maximumFractionDigits: unit,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(unit)}`;
  }
}

function AccountIcon({ type }) {
  const item = ACCOUNT_TYPES.find((entry) => entry.value === type);
  const Icon = item?.icon || Wallet;
  return <Icon size={21} />;
}

export default function Accounts() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;
  const workspaceCurrency = activeWorkspace?.currency || "KES";

  const {
    accounts,
    balances,
    loading,
    error: accountsError,
    refreshAccounts,
  } = useAccounts();

  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [createMessage, setCreateMessage] = useState(null);
  const [accountMessages, setAccountMessages] = useState({});
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingAccountId, setEditingAccountId] = useState(null);
  const [editForm, setEditForm] = useState({
    name: "",
    account_type: "bank",
    currency: "KES",
  });
  const [actionSaving, setActionSaving] = useState("");
  const [expandedActionsId, setExpandedActionsId] = useState(null);

  const [form, setForm] = useState({
    name: "",
    account_type: "bank",
    currency: activeWorkspace?.currency || "KES",
  });

  const loadAccounts = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);

    setLoadError("");

    try {
      await refreshAccounts();
    } catch (err) {
      setLoadError(err.message || "Could not load your accounts.");
    } finally {
      setRefreshing(false);
    }
  }, [refreshAccounts]);

  useEffect(() => {
    if (accountsError) setLoadError(accountsError);
  }, [accountsError]);

  function setAccountMessage(accountId, type, message) {
    setAccountMessages((current) => ({
      ...current,
      [accountId]: { type, message },
    }));
  }

  function clearAccountMessage(accountId) {
    setAccountMessages((current) => {
      const next = { ...current };
      delete next[accountId];
      return next;
    });
  }

  const balanceByLedger = useMemo(() => {
    return new Map(
      balances.map((item) => [item.ledger_account_id, item])
    );
  }, [balances]);

  const totalAccounts = accounts.length;
  const activeAccounts = accounts.filter((item) => item.is_active).length;

  async function handleCreate(event) {
    event.preventDefault();
    setCreateMessage(null);

    if (!workspaceId) {
      setCreateMessage({
        type: "error",
        message: "No active workspace is available.",
      });
      return;
    }

    const name = form.name.trim();

    if (!name) {
      setCreateMessage({
        type: "error",
        message: "Please enter an account name.",
      });
      return;
    }

    setSaving(true);

    try {
      await financeApi.createAccount({
        workspace_id: workspaceId,
        name,
        account_type: form.account_type,
        currency: form.currency,
      });

      setForm({
        name: "",
        account_type: "bank",
        currency: workspaceCurrency,
      });

      setShowForm(false);
      setCreateMessage({
        type: "success",
        message: "Your account was created successfully.",
      });
      await loadAccounts(true);
    } catch (err) {
      setCreateMessage({
        type: "error",
        message: err.message || "Account creation failed.",
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveEdit(account) {
    const name = editForm.name.trim();

    if (!name || !workspaceId) {
      setAccountMessage(account.id, "error", "Enter a valid account name.");
      return;
    }

    setActionSaving(account.id);
    clearAccountMessage(account.id);

    try {
      const payload = account.has_records
        ? { workspace_id: workspaceId, name }
        : {
            workspace_id: workspaceId,
            name,
            account_type: editForm.account_type,
            currency: editForm.currency,
          };

      await financeApi.updateAccount(account.id, payload);
      setEditingAccountId(null);
      setAccountMessage(
        account.id,
        "success",
        account.has_records
          ? "Account renamed successfully."
          : "Account details updated successfully."
      );
      await loadAccounts(true);
    } catch (err) {
      setAccountMessage(
        account.id,
        "error",
        err.message || "Could not update this account."
      );
    } finally {
      setActionSaving("");
    }
  }

  async function handleToggleActive(account) {
    if (!workspaceId) return;

    const action = account.is_active ? "close" : "reopen";
    const confirmed = window.confirm(
      account.is_active
        ? `Close "${account.name}"? Its balance and transaction history will be preserved.`
        : `Reopen "${account.name}"?`
    );

    if (!confirmed) return;

    setActionSaving(account.id);
    clearAccountMessage(account.id);

    try {
      await financeApi.updateAccount(account.id, {
        workspace_id: workspaceId,
        is_active: !account.is_active,
      });
      setAccountMessage(
        account.id,
        "success",
        action === "close"
          ? "Account closed. Its history and balance are preserved."
          : "Account reopened successfully."
      );
      await loadAccounts(true);
    } catch (err) {
      setAccountMessage(
        account.id,
        "error",
        err.message || `Could not ${action} this account.`
      );
    } finally {
      setActionSaving("");
    }
  }

  async function handleDelete(account) {
    if (!workspaceId) return;

    const confirmed = window.confirm(
      `Permanently delete "${account.name}"? This is only allowed if the account has no financial records. This action cannot be undone.`
    );

    if (!confirmed) return;

    setActionSaving(account.id);
    clearAccountMessage(account.id);

    try {
      await financeApi.deleteAccount(account.id, workspaceId);
      setAccountMessage(
        account.id,
        "success",
        "Unused account permanently deleted."
      );
      await loadAccounts(true);
    } catch (err) {
      setAccountMessage(
        account.id,
        "error",
        err.message || "Could not delete this account. Close it instead if it has financial history."
      );
    } finally {
      setActionSaving("");
    }
  }

  return (
    <div className="accounts-page">
      <section className="accounts-heading">
        <div>
          <div className="eyebrow">YOUR MONEY, ORGANIZED</div>
          <h1>Accounts</h1>
          <p>Manage your cash, bank accounts, mobile money and investments.</p>
        </div>

        <div className="accounts-actions">
          <button
            className="accounts-refresh"
            type="button"
            onClick={() => loadAccounts(true)}
            disabled={loading || refreshing}
            aria-label="Refresh accounts"
          >
            <RefreshCw size={17} className={refreshing ? "spinning" : ""} />
          </button>

          <button
            className="primary-button"
            type="button"
            onClick={() => {
              setCreateMessage(null);
              setShowForm((value) => !value);
            }}
          >
            {showForm ? <X size={17} /> : <Plus size={18} />}
            {showForm ? "Close" : "Add account"}
          </button>
        </div>
      </section>

      {loadError && (
        <div className="accounts-message error-message" role="alert">
          <AlertCircle size={17} />
          {loadError}
        </div>
      )}

      <section className="accounts-summary">
        <article className="accounts-summary-card">
          <div className="accounts-summary-icon">
            <Wallet size={21} />
          </div>
          <div>
            <span>Total accounts</span>
            <strong>{loading ? <Skeleton width="48px" height={22} /> : totalAccounts}</strong>
          </div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon active-icon">
            <CheckCircle2 size={21} />
          </div>
          <div>
            <span>Active accounts</span>
            <strong>{loading ? <Skeleton width="48px" height={22} /> : activeAccounts}</strong>
          </div>
        </article>
      </section>

      <TransactionEntryForm
        allowedTypes={["income", "transfer", "opening"]}
        initialType="income"
        title="Record account activity"
        description="Record income, transfer money between accounts, or enter an opening balance."
        onRecorded={() => loadAccounts(true)}
      />

      {showForm && (
        <section className="panel account-form-panel">
          <div className="panel-heading">
            <div>
              <h3>Create a financial account</h3>
              <p>Add an account to start tracking its balance.</p>
            </div>
          </div>

          <form className="account-form" onSubmit={handleCreate}>
            {createMessage && (
              <div
                className={`accounts-message ${createMessage.type === "error" ? "error-message" : "success-message"}`}
                role={createMessage.type === "error" ? "alert" : "status"}
              >
                {createMessage.type === "error" ? (
                  <AlertCircle size={17} />
                ) : (
                  <CheckCircle2 size={17} />
                )}
                {createMessage.message}
              </div>
            )}

            <label>
              Account name
              <input
                type="text"
                placeholder="e.g. M-PESA, Equity Bank, Cash wallet"
                maxLength={150}
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
                required
              />
            </label>

            <label>
              Account type
              <select
                value={form.account_type}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    account_type: event.target.value,
                  }))
                }
              >
                {ACCOUNT_TYPES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              {(() => {
                const selectedType = ACCOUNT_TYPES.find(
                  (item) => item.value === form.account_type
                );
                return selectedType ? (
                  <div className="account-type-guidance">
                    <strong>{selectedType.description}</strong>
                    <span>{selectedType.examples}</span>
                  </div>
                ) : null;
              })()}
            </label>

            <label>
              Currency
              <select
                value={form.currency}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    currency: event.target.value,
                  }))
                }
              >
                {CURRENCIES.map((currency) => (
                  <option key={currency} value={currency}>
                    {currency}
                  </option>
                ))}
              </select>
            </label>

            <div className="account-type-lock-warning">
              <strong>Choose carefully:</strong> Select the type that reflects
              the account's actual purpose. PesaPilot may prevent changing an
              account's type after transactions have been recorded, to protect
              your accounting history.
            </div>

            <div className="account-form-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setShowForm(false)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                className="primary-button"
                type="submit"
                disabled={saving}
              >
                {saving ? "Creating..." : "Create account"}
              </button>
            </div>
          </form>
        </section>
      )}

      {!showForm && createMessage && (
        <div
          className={`accounts-message ${createMessage.type === "error" ? "error-message" : "success-message"}`}
          role={createMessage.type === "error" ? "alert" : "status"}
        >
          {createMessage.type === "error" ? (
            <AlertCircle size={17} />
          ) : (
            <CheckCircle2 size={17} />
          )}
          {createMessage.message}
        </div>
      )}

      <section className="accounts-list-section">
        <div className="accounts-list-heading">
          <div>
            <h2>Your financial accounts</h2>
            <p>Balances are calculated from your posted accounting entries.</p>
          </div>
          <span>{totalAccounts} {totalAccounts === 1 ? "account" : "accounts"}</span>
        </div>

        {loading ? (
          <div className="accounts-grid accounts-skeleton-grid" aria-hidden="true">
            {Array.from({ length: 3 }, (_, index) => (
              <article className="account-card account-skeleton-card" key={index}>
                <div className="account-card-top">
                  <Skeleton width={43} height={43} radius={11} />
                  <Skeleton width={62} height={22} radius={20} />
                </div>
                <div className="account-card-name">
                  <Skeleton width="65%" height={15} />
                  <Skeleton width="38%" height={11} />
                </div>
                <div className="account-card-balance">
                  <Skeleton width="35%" height={11} />
                  <Skeleton width="72%" height={25} />
                </div>
                <div className="account-card-footer">
                  <Skeleton width={38} height={10} />
                </div>
                <div className="account-card-actions">
                  <Skeleton width="100%" height={36} />
                  <Skeleton width="100%" height={36} />
                </div>
              </article>
            ))}
          </div>
        ) : accounts.length === 0 ? (
          <div className="accounts-empty">
            <div className="accounts-empty-icon">
              <Wallet size={27} />
            </div>
            <strong>No financial accounts yet</strong>
            <p>Create your first account to begin organizing your money.</p>
            <button
              className="primary-button"
              type="button"
              onClick={() => setShowForm(true)}
            >
              <Plus size={17} />
              Add your first account
            </button>
          </div>
        ) : (
          <div className="accounts-grid">
            {accounts.map((account) => {
              const ledger = account.ledger_account
                ? balanceByLedger.get(account.ledger_account.id)
                : null;

              const typeLabel = ACCOUNT_TYPES.find(
                (item) => item.value === account.account_type
              )?.label || account.account_type;


  return (
                <article
                  className={`account-card ${!account.is_active ? "is-closed" : ""}`}
                  key={account.id}
                >
                  <div className="account-card-top">
                    <div className={`account-type-icon account-private-detail ${account.account_type}`}>
                      <AccountIcon type={account.account_type} />
                    </div>
                    <span className={`account-status ${account.is_active ? "is-active" : "is-inactive"}`}>
                      {account.is_active ? "Active" : "Inactive"}
                    </span>
                  </div>

                  <div className="account-card-name">
                    {editingAccountId === account.id ? (
                      <form
                        className="account-edit-form"
                        onSubmit={(event) => {
                          event.preventDefault();
                          handleSaveEdit(account);
                        }}
                      >
                        <label>
                          Account name
                          <input
                            type="text"
                            value={editForm.name}
                            maxLength={150}
                            onChange={(event) => setEditForm(current => ({
                              ...current, name: event.target.value
                            }))}
                            required
                          />
                        </label>
                        {!account.has_records && (
                          <>
                            <label>
                              Account type
                              <select
                                value={editForm.account_type}
                                onChange={(event) => setEditForm(current => ({
                                  ...current, account_type: event.target.value
                                }))}
                              >
                                {ACCOUNT_TYPES.map(item => (
                                  <option key={item.value} value={item.value}>
                                    {item.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label>
                              Currency
                              <select
                                value={editForm.currency}
                                onChange={(event) => setEditForm(current => ({
                                  ...current, currency: event.target.value
                                }))}
                              >
                                {CURRENCIES.map(currency => (
                                  <option key={currency} value={currency}>
                                    {currency}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </>
                        )}
                        <div className="account-edit-actions">
                          <button
                            className="secondary-button"
                            type="submit"
                            disabled={actionSaving === account.id}
                          >
                            <Check size={15} />
                            Save changes
                          </button>
                          <button
                            className="secondary-button"
                            type="button"
                            onClick={() => setEditingAccountId(null)}
                            disabled={actionSaving === account.id}
                          >
                            Cancel
                          </button>
                        </div>
                      </form>
                    ) : (
                      <>
                        <h3>{account.name}</h3>
                        <span className="account-private-detail">{typeLabel}</span>
                      </>
                    )}
                  </div>

                  <div className="account-card-balance account-private-detail">
                    <span>Current balance</span>
                    <strong>
                      {formatMoney(ledger?.balance_minor ?? 0, account.currency)}
                    </strong>
                  </div>

                  <div className="account-card-footer account-private-detail">
                    <span>{account.currency}</span>
                    {/* Ledger status intentionally hidden from the account card. */}
                  </div>

                  <div className="account-card-actions">
                    <button
                      className="account-actions-toggle"
                      type="button"
                      aria-label={expandedActionsId === account.id ? "Hide account actions" : "Show account actions"}
                      aria-expanded={expandedActionsId === account.id}
                      onClick={() => setExpandedActionsId(
                        current => current === account.id ? null : account.id
                      )}
                      disabled={Boolean(actionSaving) || editingAccountId === account.id}
                    >
                      <span>Actions</span>
                      <ChevronDown
                        size={16}
                        className={expandedActionsId === account.id ? "is-expanded" : ""}
                      />
                    </button>

                    {expandedActionsId === account.id && editingAccountId !== account.id && (
                      <div className="account-actions-menu">
                        <button
                          className="secondary-button"
                          type="button"
                          onClick={() => {
                            setEditingAccountId(account.id);
                            setExpandedActionsId(null);
                            setEditForm({
                              name: account.name || "",
                              account_type: account.account_type || "bank",
                              currency: account.currency || workspaceCurrency,
                            });
                            clearAccountMessage(account.id);
                          }}
                          disabled={Boolean(actionSaving)}
                        >
                          <Pencil size={15} />
                          {account.has_records ? "Rename" : "Edit"}
                        </button>

                        <button
                          className="secondary-button"
                          type="button"
                          onClick={() => handleToggleActive(account)}
                          disabled={Boolean(actionSaving)}
                        >
                          {account.is_active ? (
                            <><Archive size={15} /> Close</>
                          ) : (
                            <><RotateCcw size={15} /> Reopen</>
                          )}
                        </button>

                        {!account.has_records && (
                          <button
                            className="secondary-button account-delete-button"
                            type="button"
                            onClick={() => handleDelete(account)}
                            disabled={Boolean(actionSaving)}
                          >
                            <Trash2 size={15} />
                            Delete
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {accountMessages[account.id] && (
                    <div
                      className={`accounts-message ${accountMessages[account.id].type === "error" ? "error-message" : "success-message"}`}
                      role={accountMessages[account.id].type === "error" ? "alert" : "status"}
                    >
                      {accountMessages[account.id].type === "error" ? (
                        <AlertCircle size={17} />
                      ) : (
                        <CheckCircle2 size={17} />
                      )}
                      {accountMessages[account.id].message}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>



    </div>
  );
}
