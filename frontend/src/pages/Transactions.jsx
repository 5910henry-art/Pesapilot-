import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshCw, ReceiptText, CheckCircle2, AlertCircle, Clock3, RotateCcw,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { financeApi } from "../api/client";
import { Skeleton } from "../components/Skeleton";

function getEntrySortTime(entry) {
  const value = entry.created_at || entry.posted_at || entry.transaction_date;
  if (!value) return 0;

  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function formatDateTime(entry) {
  const timestamp = entry.created_at || entry.posted_at;
  const value = timestamp || entry.transaction_date;

  if (!value) return { date: "—", time: "Time unavailable" };

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { date: "—", time: "Time unavailable" };
  }

  return {
    date: date.toLocaleDateString(undefined, {
      day: "numeric", month: "short", year: "numeric",
    }),
    time: timestamp
      ? date.toLocaleTimeString(undefined, {
          hour: "numeric",
          minute: "2-digit",
        })
      : "Time unavailable",
  };
}

function formatMinorAmount(amountMinor, currency) {
  const digits = currency === 'JPY' || currency === 'KRW' ? 0 : 2;
  const amount = Number(amountMinor || 0) / (10 ** digits);

  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
}

export default function Transactions() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;
  const workspaceCurrency = activeWorkspace?.currency || "KES";
  const { accounts } = useAccounts();

  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [rowMessages, setRowMessages] = useState({});
  const [statusFilter, setStatusFilter] = useState("all");
  const [accountTypeFilter, setAccountTypeFilter] = useState("all");
  const [accountFilter, setAccountFilter] = useState("all");
  const [currentPage, setCurrentPage] = useState(1);

  const loadData = useCallback(async (quiet = false) => {
    if (!workspaceId) {
      setEntries([]);
      setLoading(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const entryData = await financeApi.listEntries(
        workspaceId,
        { limit: 100 }
      );

      const sortedEntries = [...(entryData.entries || [])].sort(
        (a, b) => getEntrySortTime(b) - getEntrySortTime(a)
      );
      setEntries(sortedEntries);
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

  const ACCOUNT_TYPE_LABELS = {
    cash: "Cash",
    bank: "Bank account",
    mobile_money: "Mobile money",
    savings: "Savings",
    investment: "Investment",
    credit: "Credit / liability",
  };

  const accountTypeByLedgerId = new Map(
    accounts
      .filter((account) => account.ledger_account?.id)
      .map((account) => [
        String(account.ledger_account.id),
        account.account_type,
      ])
  );

  const accountByLedgerId = new Map(
    accounts
      .filter((account) => account.ledger_account?.id)
      .map((account) => [
        String(account.ledger_account.id),
        account,
      ])
  );

  function getMoneyMovement(entry) {
    const moneyLines = (entry.lines || []).filter((line) => {
      const account = accountByLedgerId.get(
        String(line.ledger_account_id)
      );
      return account && account.account_type !== "credit";
    });

    const debitTotal = moneyLines.reduce(
      (sum, line) => sum + Number(line.functional_debit_minor || 0),
      0
    );

    const creditTotal = moneyLines.reduce(
      (sum, line) => sum + Number(line.functional_credit_minor || 0),
      0
    );

    const fromAccounts = [...new Set(
      moneyLines
        .filter((line) => Number(line.functional_credit_minor || 0) > 0)
        .map((line) => accountByLedgerId.get(
          String(line.ledger_account_id)
        )?.name)
        .filter(Boolean)
    )];

    const toAccounts = [...new Set(
      moneyLines
        .filter((line) => Number(line.functional_debit_minor || 0) > 0)
        .map((line) => accountByLedgerId.get(
          String(line.ledger_account_id)
        )?.name)
        .filter(Boolean)
    )];

    if (debitTotal > 0 && creditTotal > 0) {
      return {
        kind: "transfer",
        label: `${fromAccounts.join(", ") || "Account"} → ${toAccounts.join(", ") || "Account"}`,
        amount: Math.min(debitTotal, creditTotal),
      };
    }

    if (debitTotal > 0) {
      return { kind: "in", label: "Money in", amount: debitTotal };
    }

    if (creditTotal > 0) {
      return { kind: "out", label: "Money out", amount: creditTotal };
    }

    return {
      kind: "neutral",
      label: "No account movement",
      amount: 0,
    };
  }

  const availableAccountTypes = [
    "cash",
    "bank",
    "mobile_money",
    "savings",
    "investment",
    "credit",
  ].filter((type) =>
    accounts.some((account) => account.account_type === type)
  );

  const availableAccounts = accounts.filter((account) =>
    account.ledger_account?.id &&
    (accountTypeFilter === "all" || account.account_type === accountTypeFilter)
  );

  const visibleEntries = entries.filter((entry) => {
    const matchesStatus =
      statusFilter === "all" || entry.status === statusFilter;

    const matchesAccountType =
      accountTypeFilter === "all" ||
      (entry.lines || []).some((line) =>
        accountTypeByLedgerId.get(String(line.ledger_account_id)) === accountTypeFilter
      );

    const matchesAccount =
      accountFilter === "all" ||
      (entry.lines || []).some(
        (line) => String(line.ledger_account_id) === accountFilter
      );

    return matchesStatus && matchesAccountType && matchesAccount;
  });

  const pageSize = 5;
  const totalPages = Math.max(1, Math.ceil(visibleEntries.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const paginatedEntries = visibleEntries.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  );

  const postedCount = entries.filter(
    (entry) => entry.status === "posted" || entry.status === "reversed"
  ).length;

  const draftCount = entries.filter(
    (entry) => entry.status === "draft"
  ).length;

  async function handleClear(entry) {
    if (!workspaceId || entry.status !== "draft") return;

    const confirmed = window.confirm(
      `Clear draft "${entry.description}"? This permanently deletes it.`
    );
    if (!confirmed) return;

    setError("");
    setRowMessages((current) => {
      const next = { ...current };
      delete next[entry.id];
      return next;
    });
    setSaving(true);
    try {
      await financeApi.clearDraftEntry(entry.id, workspaceId);
      await loadData(true);
      setRowMessages((current) => ({
        ...current,
        [entry.id]: {
          type: "success",
          message: `Draft "${entry.description}" was cleared.`,
        },
      }));
    } catch (err) {
      setRowMessages((current) => ({
        ...current,
        [entry.id]: {
          type: "error",
          message: err.message || "Draft could not be cleared.",
        },
      }));
    } finally {
      setSaving(false);
    }
  }

  async function handleReverse(entry) {
    if (!workspaceId || entry.status !== "posted") return;

    const confirmed = window.confirm(
      `Reverse "${entry.description}"? This creates a separate correcting transaction and cannot be undone.`
    );

    if (!confirmed) return;

    setError("");
    setRowMessages((current) => {
      const next = { ...current };
      delete next[entry.id];
      return next;
    });
    setSaving(true);

    try {
      await financeApi.reverseEntry(entry.id, workspaceId);
      await loadData(true);
      setRowMessages((current) => ({
        ...current,
        [entry.id]: {
          type: "success",
          message: `"${entry.description}" was reversed successfully.`,
        },
      }));
    } catch (err) {
      setRowMessages((current) => ({
        ...current,
        [entry.id]: {
          type: "error",
          message: err.message || "Transaction could not be reversed.",
        },
      }));
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
          <p>A complete history of money moving through your accounts.</p>
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

      <section className="accounts-summary transaction-summary">
        <article className="accounts-summary-card">
          <div className="accounts-summary-icon"><ReceiptText size={21} /></div>
          <div><span>Total transactions</span><strong>{loading ? <Skeleton width="48px" height={22} /> : entries.length}</strong></div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon active-icon"><CheckCircle2 size={21} /></div>
          <div><span>Posted / reversed</span><strong>{loading ? <Skeleton width="48px" height={22} /> : postedCount}</strong></div>
        </article>

        <article className="accounts-summary-card">
          <div className="accounts-summary-icon draft-icon"><Clock3 size={21} /></div>
          <div><span>Drafts</span><strong>{loading ? <Skeleton width="48px" height={22} /> : draftCount}</strong></div>
        </article>
      </section>

      <section className="panel transaction-history-panel">
        <div className="panel-heading transaction-history-heading">
          <div>
            <h3>Transaction history</h3>
            <p>Your latest 100 journal entries.</p>
          </div>

          <div className="transaction-filter-controls">
            <select
              aria-label="Filter account type"
              value={accountTypeFilter}
              onChange={(event) => {
                setAccountTypeFilter(event.target.value);
                setAccountFilter("all");
                setCurrentPage(1);
              }}
            >
              <option value="all">All account types</option>
              {availableAccountTypes.map((type) => (
                <option key={type} value={type}>
                  {ACCOUNT_TYPE_LABELS[type]}
                </option>
              ))}
            </select>

            <select
              aria-label="Filter specific account"
              value={accountFilter}
              onChange={(event) => {
                setAccountFilter(event.target.value);
                setCurrentPage(1);
              }}
            >
              <option value="all">All accounts</option>
              {availableAccounts.map((account) => (
                <option
                  key={account.id}
                  value={String(account.ledger_account.id)}
                >
                  {account.name}
                </option>
              ))}
            </select>

            <select
            aria-label="Filter transaction status"
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              setCurrentPage(1);
            }}
          >
            <option value="all">All statuses</option>
            <option value="posted">Posted</option>
            <option value="draft">Draft</option>
            <option value="reversed">Reversed</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="transaction-table-wrap transaction-skeleton-wrap" aria-hidden="true">
            <table className="transaction-table">
              <thead>
                <tr>
                  <th>DESCRIPTION</th>
                  <th>REFERENCE</th>
                  <th>DATE &amp; TIME</th>
                  <th className="transaction-amount-heading">DIRECTION / AMOUNT</th>
                  <th>STATUS</th>
                  <th>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 7 }, (_, index) => (
                  <tr key={index}>
                    <td><Skeleton width="78%" height={13} /></td>
                    <td><Skeleton width="65%" height={12} /></td>
                    <td><Skeleton width="72%" height={12} /></td>
                    <td><Skeleton width="76%" height={13} /></td>
                    <td><Skeleton width={62} height={22} radius={20} /></td>
                    <td><Skeleton width={72} height={29} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : visibleEntries.length === 0 ? (
          <div className="transaction-empty">
            <div className="accounts-empty-icon"><ReceiptText size={22} /></div>
            <strong>No transactions found</strong>
            <span>Transactions you record will appear here.</span>
          </div>
        ) : (
          <>
          <div className="transaction-table-wrap">
            <table className="transaction-table">
              <thead>
                <tr>
                  <th>DESCRIPTION</th>
                  <th>REFERENCE</th>
                  <th>DATE &amp; TIME</th>
                  <th className="transaction-amount-heading">DIRECTION / AMOUNT</th>
                  <th>STATUS</th>
                  <th>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {paginatedEntries.map((entry) => (
                  <tr key={entry.id}>
                    <td><strong>{entry.description}</strong></td>
                    <td className="transaction-reference-cell">
                      <details className="transaction-reference">
                        <summary title={entry.reference || "No reference"}>
                          {entry.reference || "—"}
                        </summary>
                        <span>{entry.reference || "No reference"}</span>
                      </details>
                    </td>
                    <td className="transaction-date-cell">
                      <strong>{formatDateTime(entry).date}</strong>
                      <span>{formatDateTime(entry).time}</span>
                    </td>
                    <td className="transaction-amount">
                      {(() => {
                        const movement = getMoneyMovement(entry);
                        return (
                          <div className="transaction-movement">
                            {movement.kind === "transfer" && (
                              <span className="transaction-direction direction-transfer">
                                {movement.label}
                              </span>
                            )}
                            <strong className={`transaction-value direction-${movement.kind}`}>
                              {movement.kind === "in" ? "+" :
                               movement.kind === "out" ? "−" : ""}
                              {formatMinorAmount(movement.amount, workspaceCurrency)}
                            </strong>
                          </div>
                        );
                      })()}
                    </td>
                    <td>
                      <span className={`transaction-status status-${entry.status}`}>
                        {entry.status}
                      </span>
                    </td>
                    <td>
                      <div className="transaction-row-action">
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
                        ) : entry.status === "draft" ? (
                          <button
                            type="button"
                            className="transaction-reverse-button"
                            onClick={() => handleClear(entry)}
                            disabled={saving}
                            title="Permanently clear this draft"
                          >
                            Clear
                          </button>
                        ) : (
                          <span className="transaction-action-muted">—</span>
                        )}

                        {rowMessages[entry.id] && (
                          <div
                            className={`transaction-row-message ${rowMessages[entry.id].type}`}
                            role={rowMessages[entry.id].type === "error" ? "alert" : "status"}
                          >
                            {rowMessages[entry.id].type === "error"
                              ? <AlertCircle size={13} />
                              : <CheckCircle2 size={13} />}
                            <span>{rowMessages[entry.id].message}</span>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="transaction-pagination">
            <span className="transaction-page-count">
              Showing {(safePage - 1) * pageSize + 1}–
              {Math.min(safePage * pageSize, visibleEntries.length)}
              {' '}of {visibleEntries.length} transactions
            </span>
            <div className="transaction-page-controls">
              <button
                type="button"
                onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                disabled={safePage === 1}
              >
                Previous
              </button>
              {Array.from({ length: totalPages }, (_, index) => index + 1).map((page) => (
                <button
                  type="button"
                  key={page}
                  className={safePage === page ? "active" : ""}
                  aria-current={safePage === page ? "page" : undefined}
                  onClick={() => setCurrentPage(page)}
                >
                  {page}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
                disabled={safePage === totalPages}
              >
                Next
              </button>
            </div>
          </div>
          </>
        )}
      </section>

    </div>
  );
}
