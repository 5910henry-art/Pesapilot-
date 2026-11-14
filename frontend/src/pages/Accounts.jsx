import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Wallet, Plus, RefreshCw, Building2, Smartphone,
  PiggyBank, TrendingUp, CreditCard, Banknote,
  X, CheckCircle2, AlertCircle
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { financeApi } from "../api/client";

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
  { value: "cash", label: "Cash", icon: Banknote },
  { value: "bank", label: "Bank account", icon: Building2 },
  { value: "mobile_money", label: "Mobile money", icon: Smartphone },
  { value: "savings", label: "Savings", icon: PiggyBank },
  { value: "investment", label: "Investment", icon: TrendingUp },
  { value: "credit", label: "Credit / liability", icon: CreditCard },
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

  const [accounts, setAccounts] = useState([]);
  const [balances, setBalances] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({
    name: "",
    account_type: "bank",
    currency: activeWorkspace?.currency || "KES",
  });

  const loadAccounts = useCallback(async (quiet = false) => {
    if (!workspaceId) {
      setAccounts([]);
      setBalances([]);
      setLoading(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const [accountData, balanceData] = await Promise.all([
        financeApi.listAccounts(workspaceId),
        financeApi.getBalances(workspaceId),
      ]);

      setAccounts(accountData.accounts || []);
      setBalances(balanceData.accounts || []);
    } catch (err) {
      setError(err.message || "Could not load your accounts.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);


  const balanceByLedger = useMemo(() => {
    return new Map(
      balances.map((item) => [item.ledger_account_id, item])
    );
  }, [balances]);

  const totalAccounts = accounts.length;
  const activeAccounts = accounts.filter((item) => item.is_active).length;

  async function handleCreate(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!workspaceId) {
      setError("No active workspace is available.");
      return;
    }

    const name = form.name.trim();

    if (!name) {
      setError("Please enter an account name.");
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
      setSuccess("Your account was created successfully.");
      await loadAccounts(true);
    } catch (err) {
      setError(err.message || "Account creation failed.");
    } finally {
      setSaving(false);
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
              setError("");
              setSuccess("");
              setShowForm((value) => !value);
            }}
          >
            {showForm ? <X size={17} /> : <Plus size={18} />}
            {showForm ? "Close" : "Add account"}
          </button>
        </div>
      </section>

      {error && (
        <div className="accounts-message error-message" role="alert">
          <AlertCircle size={17} />
          {error}
        </div>
      )}

      {success && (
        <div className="accounts-message success-message" role="status">
          <CheckCircle2 size={17} />
          {success}
        </div>
      )}

      <section className="accounts-summary">
        <article className="accounts-summary-card">
          <div className="accounts-summary-icon">
            <Wallet size={21} />
          </div>
          <div>
            <span>Total accounts</span>
            <strong>{loading ? "—" : totalAccounts}</strong>
          </div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon active-icon">
            <CheckCircle2 size={21} />
          </div>
          <div>
            <span>Active accounts</span>
            <strong>{loading ? "—" : activeAccounts}</strong>
          </div>
        </article>
      </section>

      {showForm && (
        <section className="panel account-form-panel">
          <div className="panel-heading">
            <div>
              <h3>Create a financial account</h3>
              <p>Add an account to start tracking its balance.</p>
            </div>
          </div>

          <form className="account-form" onSubmit={handleCreate}>
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

      <section className="accounts-list-section">
        <div className="accounts-list-heading">
          <div>
            <h2>Your financial accounts</h2>
            <p>Balances are calculated from your posted accounting entries.</p>
          </div>
          <span>{totalAccounts} {totalAccounts === 1 ? "account" : "accounts"}</span>
        </div>

        {loading ? (
          <div className="accounts-empty">
            <RefreshCw size={24} className="spinning" />
            <strong>Loading your accounts...</strong>
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
                <article className="account-card" key={account.id}>
                  <div className="account-card-top">
                    <div className={`account-type-icon ${account.account_type}`}>
                      <AccountIcon type={account.account_type} />
                    </div>
                    <span className={`account-status ${account.is_active ? "is-active" : "is-inactive"}`}>
                      {account.is_active ? "Active" : "Inactive"}
                    </span>
                  </div>

                  <div className="account-card-name">
                    <h3>{account.name}</h3>
                    <span>{typeLabel}</span>
                  </div>

                  <div className="account-card-balance">
                    <span>Current balance</span>
                    <strong>
                      {formatMoney(ledger?.balance_minor ?? 0, account.currency)}
                    </strong>
                  </div>

                  <div className="account-card-footer">
                    <span>{account.currency}</span>
                    <span>
                      {ledger ? "Ledger connected" : "No ledger balance yet"}
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
