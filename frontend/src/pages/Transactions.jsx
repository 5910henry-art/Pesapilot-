import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDownLeft, ArrowUpRight, ArrowLeftRight, Plus, RefreshCw,
  ReceiptText, CheckCircle2, AlertCircle, Clock3, Wallet, RotateCcw,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { financeApi } from "../api/client";

const MINOR_UNITS = {
  KES: 2, USD: 2, EUR: 2, GBP: 2, UGX: 0, TZS: 2, RWF: 0,
  ZAR: 2, NGN: 2, GHS: 2, INR: 2, CAD: 2, AUD: 2, CHF: 2,
  JPY: 0, CNY: 2, AED: 2, SAR: 2,
};

const TYPES = [
  { value: "income", label: "Income", icon: ArrowDownLeft },
  { value: "expense", label: "Expense", icon: ArrowUpRight },
  { value: "transfer", label: "Transfer", icon: ArrowLeftRight },
  { value: "opening", label: "Opening balance", icon: Wallet },
];

function localDateValue() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
}

function toMinorUnits(value, currency) {
  const text = String(value || "").trim();
  const digits = MINOR_UNITS[currency] ?? 2;

  if (!/^\d+(\.\d+)?$/.test(text)) return null;

  const [whole, fraction = ""] = text.split(".");
  if (fraction.length > digits) return null;

  const normalized = `${whole}${digits ? fraction.padEnd(digits, "0") : ""}`;
  const amount = Number(normalized);

  return Number.isSafeInteger(amount) && amount > 0 ? amount : null;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, {
        day: "numeric", month: "short", year: "numeric",
      });
}

export default function Transactions() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;
  const workspaceCurrency = activeWorkspace?.currency || "KES";

  const [accounts, setAccounts] = useState([]);
  const [ledgerAccounts, setLedgerAccounts] = useState([]);
  const [expenseCategories, setExpenseCategories] = useState([]);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [form, setForm] = useState({
    type: "income",
    accountId: "",
    secondAccountId: "",
    amount: "",
    description: "",
    date: localDateValue(),
    exchangeRate: "",
    categoryId: "",
  });

  const loadData = useCallback(async (quiet = false) => {
    if (!workspaceId) {
      setAccounts([]);
      setLedgerAccounts([]);
      setExpenseCategories([]);
      setEntries([]);
      setLoading(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const [accountData, balanceData, categoryData, entryData] = await Promise.all([
        financeApi.listAccounts(workspaceId),
        financeApi.getBalances(workspaceId),
        financeApi.listExpenseCategories(workspaceId),
        financeApi.listEntries(workspaceId, { limit: 100 }),
      ]);

      setAccounts(accountData.accounts || []);
      setLedgerAccounts(balanceData.accounts || []);
      setExpenseCategories(
        (categoryData.categories || []).filter(
          (category) => category.is_active && category.ledger_account?.id
        )
      );
      setEntries(entryData.entries || []);
    } catch (err) {
      setError(err.message || "Could not load your transactions.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const usableAccounts = useMemo(
    () => accounts.filter(
      (account) =>
        account.is_active &&
        account.account_type !== "credit" &&
        account.ledger_account?.id
    ),
    [accounts]
  );

  const systemAccounts = useMemo(() => ({
    income: ledgerAccounts.find((item) => item.code === "4000" && item.is_active),
    equity: ledgerAccounts.find((item) => item.code === "3000" && item.is_active),
  }), [ledgerAccounts]);

  const effectiveAccountId =
    form.accountId || usableAccounts[0]?.id || "";

  const effectiveCategoryId =
    expenseCategories.some((category) => category.id === form.categoryId)
      ? form.categoryId
      : expenseCategories[0]?.id || "";

  const effectiveSecondAccountId =
    form.secondAccountId ||
    usableAccounts.find((item) => item.id !== effectiveAccountId)?.id ||
    "";

  const selectedExpenseCategory = expenseCategories.find(
    (category) => category.id === effectiveCategoryId
  );

  const selectedAccount = usableAccounts.find(
    (item) => item.id === effectiveAccountId
  );

  const secondAccount = usableAccounts.find(
    (item) => item.id === effectiveSecondAccountId
  );

  const needsRate = Boolean(
    selectedAccount && selectedAccount.currency !== workspaceCurrency
  );

  const visibleEntries = entries.filter(
    (entry) => statusFilter === "all" || entry.status === statusFilter
  );

  const postedCount = entries.filter(
    (entry) => entry.status === "posted" || entry.status === "reversed"
  ).length;

  const draftCount = entries.filter(
    (entry) => entry.status === "draft"
  ).length;

  function changeType(type) {
    setError("");
    setSuccess("");

    setForm((current) => ({
      ...current,
      type,
      amount: "",
      description: "",
      exchangeRate: "",
      categoryId: "",
    }));
  }

  async function handleReverse(entry) {
    if (!workspaceId || entry.status !== "posted") return;

    const confirmed = window.confirm(
      `Reverse "${entry.description}"? This creates a separate correcting transaction and cannot be undone.`
    );

    if (!confirmed) return;

    setError("");
    setSuccess("");
    setSaving(true);

    try {
      await financeApi.reverseEntry(entry.id, workspaceId);
      setSuccess(`"${entry.description}" was reversed successfully.`);
      await loadData(true);
    } catch (err) {
      setError(err.message || "Transaction could not be reversed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!workspaceId) {
      setError("No active workspace is available.");
      return;
    }

    if (!selectedAccount) {
      setError("Create an active financial account first.");
      return;
    }

    if (form.type === "expense" && !selectedExpenseCategory) {
      setError("Create or select an active expense category before recording this expense.");
      return;
    }

    if (!form.description.trim()) {
      setError("Enter a transaction description.");
      return;
    }

    const digits = MINOR_UNITS[selectedAccount.currency] ?? 2;
    const amountMinor = toMinorUnits(form.amount, selectedAccount.currency);

    if (amountMinor === null) {
      setError(
        `Enter a valid amount in ${selectedAccount.currency}` +
        (digits === 0 ? " (whole numbers only)." : ` (up to ${digits} decimal places).`)
      );
      return;
    }

    if (
      needsRate &&
      (
        !form.exchangeRate.trim() ||
        !/^\d+(\.\d{1,12})?$/.test(form.exchangeRate) ||
        Number(form.exchangeRate) <= 0
      )
    ) {
      setError(
        `Enter the ${workspaceCurrency} exchange rate for 1 ${selectedAccount.currency}.`
      );
      return;
    }

    const line = (ledgerId, side, rate) => ({
      ledger_account_id: ledgerId,
      debit_minor: side === "debit" ? amountMinor : 0,
      credit_minor: side === "credit" ? amountMinor : 0,
      ...(rate ? { exchange_rate: rate } : {}),
    });

    let lines = [];

    if (form.type === "income") {
      if (!systemAccounts.income) {
        setError("The Income ledger account is unavailable. Refresh and try again.");
        return;
      }

      lines = [
        line(
          selectedAccount.ledger_account.id,
          "debit",
          needsRate ? form.exchangeRate : null
        ),
        line(systemAccounts.income.ledger_account_id, "credit"),
      ];
    } else if (form.type === "expense") {
      if (!selectedExpenseCategory?.ledger_account?.id) {
        setError("The selected expense category ledger is unavailable. Refresh and try again.");
        return;
      }

      lines = [
        line(selectedExpenseCategory.ledger_account.id, "debit"),
        line(
          selectedAccount.ledger_account.id,
          "credit",
          needsRate ? form.exchangeRate : null
        ),
      ];
    } else if (form.type === "opening") {
      if (!systemAccounts.equity) {
        setError("The Owner's Equity ledger account is unavailable. Refresh and try again.");
        return;
      }

      lines = [
        line(
          selectedAccount.ledger_account.id,
          "debit",
          needsRate ? form.exchangeRate : null
        ),
        line(systemAccounts.equity.ledger_account_id, "credit"),
      ];
    } else {
      if (!secondAccount || secondAccount.id === selectedAccount.id) {
        setError("Choose two different accounts for the transfer.");
        return;
      }

      if (secondAccount.currency !== selectedAccount.currency) {
        setError(
          "Transfers between different currencies are not supported yet. Choose accounts with the same currency."
        );
        return;
      }

      lines = [
        line(secondAccount.ledger_account.id, "debit"),
        line(selectedAccount.ledger_account.id, "credit"),
      ];
    }

    const transactionDate = new Date(
      `${form.date}T12:00:00`
    ).toISOString();

    const reference = `PP-${Date.now()}`;

    setSaving(true);

    try {
      const created = await financeApi.createEntry({
        workspace_id: workspaceId,
        reference,
        description: form.description.trim(),
        transaction_date: transactionDate,
        lines,
        ...(form.type === "expense" ? { category_id: effectiveCategoryId } : {}),
      });

      const entryId = created.entry?.id;

      if (!entryId) {
        throw new Error(
          "The draft was created but the server did not return its transaction ID."
        );
      }

      try {
        await financeApi.postEntry(entryId, workspaceId);

        setSuccess("Transaction recorded and posted successfully.");

        setForm((current) => ({
          ...current,
          amount: "",
          description: "",
          exchangeRate: "",
          categoryId: "",
          date: localDateValue(),
        }));
      } catch (postError) {
        setSuccess(
          `Draft saved, but it was not posted: ${postError.message}`
        );
      }

      await loadData(true);
    } catch (err) {
      setError(err.message || "Transaction could not be recorded.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="transactions-page">
      <section className="accounts-heading">
        <div>
          <div className="eyebrow">TRACK EVERY SHILLING</div>
          <h1>Transactions</h1>
          <p>Record income, expenses, transfers and starting balances.</p>
        </div>

        <button
          className="accounts-refresh"
          type="button"
          onClick={() => loadData(true)}
          disabled={loading || refreshing}
          aria-label="Refresh transactions"
        >
          <RefreshCw size={17} className={refreshing ? "spinning" : ""} />
        </button>
      </section>

      {error && (
        <div className="accounts-message error-message" role="alert">
          <AlertCircle size={17} />{error}
        </div>
      )}

      {success && (
        <div className="accounts-message success-message" role="status">
          <CheckCircle2 size={17} />{success}
        </div>
      )}

      <section className="accounts-summary transaction-summary">
        <article className="accounts-summary-card">
          <div className="accounts-summary-icon"><ReceiptText size={21} /></div>
          <div><span>Total transactions</span><strong>{loading ? "—" : entries.length}</strong></div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon active-icon"><CheckCircle2 size={21} /></div>
          <div><span>Posted / reversed</span><strong>{loading ? "—" : postedCount}</strong></div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon draft-icon"><Clock3 size={21} /></div>
          <div><span>Drafts</span><strong>{loading ? "—" : draftCount}</strong></div>
        </article>
      </section>

      <section className="panel transaction-form-panel">
        <div className="panel-heading">
          <div>
            <h3>Record a transaction</h3>
            <p>Entries are posted to your double-entry ledger.</p>
          </div>
        </div>

        <div className="transaction-type-tabs" role="group" aria-label="Transaction type">
          {TYPES.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              className={form.type === value ? "transaction-type active" : "transaction-type"}
              onClick={() => changeType(value)}
            >
              <Icon size={17} /><span>{label}</span>
            </button>
          ))}
        </div>

        {usableAccounts.length === 0 && !loading ? (
          <div className="transaction-hint">
            <Wallet size={19} />
            <span>
              You need an active cash, bank, mobile money, savings or investment
              account before recording transactions. Add one from the Accounts page.
            </span>
          </div>
        ) : (
          <form className="transaction-form" onSubmit={handleSubmit}>
            <label>
              {form.type === "income" ? "Deposit into"
                : form.type === "expense" ? "Paid from"
                : form.type === "opening" ? "Account"
                : "Transfer from"}
              <select
                value={effectiveAccountId}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  accountId: event.target.value,
                  exchangeRate: "",
                }))}
                required
              >
                <option value="">Choose an account</option>
                {usableAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.currency}
                  </option>
                ))}
              </select>
            </label>

            {form.type === "expense" && (
              <label>
                Expense category
                <select
                  value={effectiveCategoryId}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    categoryId: event.target.value,
                  }))}
                  required
                  disabled={expenseCategories.length === 0}
                >
                  <option value="">Choose an expense category</option>
                  {expenseCategories.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.name}
                    </option>
                  ))}
                </select>
                {expenseCategories.length === 0 && (
                  <small>Create an expense category in Budgets before recording an expense.</small>
                )}
              </label>
            )}

            {form.type === "transfer" && (
              <label>
                Transfer to
                <select
                  value={effectiveSecondAccountId}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    secondAccountId: event.target.value,
                  }))}
                  required
                >
                  <option value="">Choose destination</option>
                  {usableAccounts
                    .filter((account) => account.id !== form.accountId)
                    .map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name} · {account.currency}
                      </option>
                    ))}
                </select>
              </label>
            )}

            <label>
              Amount ({selectedAccount?.currency || workspaceCurrency})
              <input
                type="number"
                inputMode="decimal"
                min="0"
                step={(MINOR_UNITS[selectedAccount?.currency || workspaceCurrency] ?? 2) === 0
                  ? "1"
                  : `0.${"0".repeat(Math.max(0, (MINOR_UNITS[selectedAccount?.currency || workspaceCurrency] ?? 2) - 1))}1`}
                placeholder="0.00"
                value={form.amount}
                onChange={(event) => setForm((current) => ({
                  ...current, amount: event.target.value,
                }))}
                required
              />
            </label>

            {needsRate && form.type !== "transfer" && (
              <label>
                Exchange rate (1 {selectedAccount.currency} = ? {workspaceCurrency})
                <input
                  type="number"
                  inputMode="decimal"
                  min="0.000000000001"
                  step="any"
                  placeholder={`Rate in ${workspaceCurrency}`}
                  value={form.exchangeRate}
                  onChange={(event) => setForm((current) => ({
                    ...current, exchangeRate: event.target.value,
                  }))}
                  required
                />
              </label>
            )}

            <label>
              Description
              <input
                type="text"
                maxLength={255}
                placeholder={
                  form.type === "income" ? "e.g. Monthly salary"
                    : form.type === "expense" ? "e.g. Groceries"
                    : form.type === "transfer" ? "e.g. Move money to savings"
                    : "e.g. Balance when starting PesaPilot"
                }
                value={form.description}
                onChange={(event) => setForm((current) => ({
                  ...current, description: event.target.value,
                }))}
                required
              />
            </label>

            <label>
              Date
              <input
                type="date"
                value={form.date}
                onChange={(event) => setForm((current) => ({
                  ...current, date: event.target.value,
                }))}
                required
              />
            </label>

            <div className="transaction-form-footer">
              <span>Workspace currency: <strong>{workspaceCurrency}</strong></span>
              <button
                className="primary-button"
                type="submit"
                disabled={saving || loading || usableAccounts.length === 0}
              >
                {saving ? "Saving transaction…" : <><Plus size={17} /> Record transaction</>}
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="panel transaction-history-panel">
        <div className="panel-heading transaction-history-heading">
          <div>
            <h3>Transaction history</h3>
            <p>Your latest 100 journal entries.</p>
          </div>

          <select
            aria-label="Filter transaction status"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="posted">Posted</option>
            <option value="draft">Draft</option>
            <option value="reversed">Reversed</option>
          </select>
        </div>

        {loading ? (
          <div className="transaction-empty">Loading transactions…</div>
        ) : visibleEntries.length === 0 ? (
          <div className="transaction-empty">
            <div className="accounts-empty-icon"><ReceiptText size={22} /></div>
            <strong>No transactions found</strong>
            <span>Transactions you record will appear here.</span>
          </div>
        ) : (
          <div className="transaction-table-wrap">
            <table className="transaction-table">
              <thead>
                <tr>
                  <th>DESCRIPTION</th>
                  <th>REFERENCE</th>
                  <th>DATE</th>
                  <th>STATUS</th>
                  <th>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {visibleEntries.map((entry) => (
                  <tr key={entry.id}>
                    <td><strong>{entry.description}</strong></td>
                    <td>{entry.reference}</td>
                    <td>{formatDate(entry.transaction_date)}</td>
                    <td>
                      <span className={`transaction-status status-${entry.status}`}>
                        {entry.status}
                      </span>
                    </td>
                    <td>
                      {entry.status === "posted" ? (
                        <button
                          type="button"
                          className="transaction-reverse-button"
                          onClick={() => handleReverse(entry)}
                          disabled={saving}
                          title="Reverse this transaction"
                        >
                          <RotateCcw size={14} />
                          Reverse
                        </button>
                      ) : entry.status === "reversed" ? (
                        <span className="transaction-action-muted">Reversed</span>
                      ) : (
                        <span className="transaction-action-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="transaction-footnote">
        Transactions use double-entry accounting. Reversals create a separate
        correcting entry; they do not erase the original record.
      </p>
    </div>
  );
}
