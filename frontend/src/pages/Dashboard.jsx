import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  PiggyBank,
  ArrowRight,
  CircleDollarSign,
  AlertCircle,
  Eye,
  EyeOff,
  RefreshCw,
  Activity,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { financeApi } from "../api/client";

function formatMoney(minor, currency, minorUnit = 2) {
  const precision = Number.isInteger(Number(minorUnit))
    ? Math.min(4, Math.max(0, Number(minorUnit)))
    : 2;

  const amount = Number(minor ?? 0) / (10 ** precision);

  if (!Number.isFinite(amount)) return "—";

  try {
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency,
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(precision)}`;
  }
}

function formatDate(value, timezone) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "—";

  try {
    return new Intl.DateTimeFormat("en-KE", {
      day: "numeric",
      month: "short",
      year: "numeric",
      ...(timezone ? { timeZone: timezone } : {}),
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat("en-KE", {
      day: "numeric",
      month: "short",
      year: "numeric",
    }).format(date);
  }
}

function getPeriodLabel(period) {
  if (!period?.start) return "This month";

  const date = new Date(`${period.start}T12:00:00Z`);

  if (Number.isNaN(date.getTime())) return "This month";

  return new Intl.DateTimeFormat("en-KE", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function formatStatus(status) {
  if (!status) return "Unknown";

  return status.charAt(0).toUpperCase() + status.slice(1);
}

export default function Dashboard() {
  const { profile, activeWorkspace } = useAuth();
  const navigate = useNavigate();

  const workspaceId = activeWorkspace?.id;

  const fallbackCurrency =
    activeWorkspace?.currency ||
    profile?.preferred_currency ||
    "KES";

  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [visibleAmounts, setVisibleAmounts] = useState({
    balance: false,
    income: false,
    expenses: false,
    savings: false,
  });

  const loadDashboard = useCallback(async ({
    refresh = false,
    isCancelled = () => false,
  } = {}) => {
    if (!workspaceId) {
      setSummary(null);
      setError("");
      setLoading(false);
      setRefreshing(false);
      return;
    }

    if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    setError("");

    try {
      const result = await financeApi.getDashboardSummary(workspaceId);

      if (isCancelled()) return;

      if (result?.success === false) {
        throw new Error(
          result.message || "Could not load your financial overview."
        );
      }

      setSummary(result);
    } catch (err) {
      if (!isCancelled()) {
        setError(
          err.message || "Could not load your financial overview."
        );
      }
    } finally {
      if (!isCancelled()) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;

    loadDashboard({
      isCancelled: () => cancelled,
    });

    return () => {
      cancelled = true;
    };
  }, [loadDashboard]);

  const currency = summary?.currency || fallbackCurrency;
  const minorUnit = summary?.minor_unit ?? 2;
  const timezone = summary?.timezone;

  const totals = summary?.summary || {};

  const money = (value) =>
    formatMoney(value, currency, minorUnit);

  const firstName = profile?.first_name || "there";

  const [greeting] = useState(() => {
    const hour = new Date().getHours();

    if (hour < 12) return "Good morning";
    if (hour < 17) return "Good afternoon";

    return "Good evening";
  });

  const income = Number(totals.monthly_income_minor || 0);
  const expenses = Number(totals.monthly_expenses_minor || 0);
  const netCashflow = income - expenses;

  const expenseRatio = income > 0
    ? Math.round((expenses / income) * 100)
    : null;

  const recentTransactions = Array.isArray(summary?.recent_transactions)
    ? summary.recent_transactions
    : [];

  const stats = [
    {
      title: "Total balance",
      visibilityKey: "balance",
      value: totals.total_assets_minor,
      note: "Across your asset accounts",
      icon: Wallet,
      color: "green",
    },
    {
      title: "Total income",
      visibilityKey: "income",
      value: totals.monthly_income_minor,
      note: "This month",
      icon: TrendingUp,
      color: "blue",
    },
    {
      title: "Total expenses",
      visibilityKey: "expenses",
      value: totals.monthly_expenses_minor,
      note: "This month",
      icon: TrendingDown,
      color: "orange",
    },
    {
      title: "Total savings",
      visibilityKey: "savings",
      value: totals.total_savings_minor,
      note: "Across savings accounts",
      icon: PiggyBank,
      color: "purple",
    },
  ];

  const periodLabel = getPeriodLabel(summary?.period);

  return (
    <div className="dashboard">

      {/* Welcome section */}
      <section className="welcome-section">
        <div>
          <div className="eyebrow">
            YOUR FINANCIAL COMMAND CENTRE
          </div>

          <h1>
            {greeting}, {firstName} <span>👋</span>
          </h1>

          <p>
            Here's an overview of your financial life.
          </p>
        </div>

        <div className="dashboard-actions">
          <button
            type="button"
            className="dashboard-refresh"
            onClick={() => loadDashboard({ refresh: true })}
            disabled={loading || refreshing || !workspaceId}
          >
            <RefreshCw
              size={16}
              className={refreshing ? "refresh-spinning" : ""}
            />
            {refreshing ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </section>

      {/* Workspace notice */}
      {!workspaceId && (
        <div className="dashboard-alert" role="status">
          <AlertCircle size={17} />
          <span>
            Select a workspace to view your financial overview.
          </span>
        </div>
      )}

      {/* Error notice */}
      {error && (
        <div className="dashboard-alert" role="alert">
          <AlertCircle size={17} />

          <span>{error}</span>

          <button
            type="button"
            onClick={() => loadDashboard()}
          >
            Retry
          </button>
        </div>
      )}

      {/* Financial statistics */}
      <section className="stats-grid">
        {stats.map(({ title, value, note, icon: Icon, color, visibilityKey }) => (
          <article className="stat-card" key={title}>
            <div className="stat-top">
              <span>{title}</span>

              <div className="stat-card-actions">
                <button
                  type="button"
                  className="stat-visibility-button"
                  onClick={() =>
                    setVisibleAmounts((current) => ({
                      ...current,
                      [visibilityKey]: !current[visibilityKey],
                    }))
                  }
                  aria-label={
                    visibleAmounts[visibilityKey]
                      ? `Hide ${title}`
                      : `Show ${title}`
                  }
                  aria-pressed={!visibleAmounts[visibilityKey]}
                  title={
                    visibleAmounts[visibilityKey]
                      ? `Hide ${title}`
                      : `Show ${title}`
                  }
                >
                  {visibleAmounts[visibilityKey]
                    ? <Eye size={17} />
                    : <EyeOff size={17} />}
                </button>

                <div className={`stat-icon ${color}`}>
                  <Icon size={20} />
                </div>
              </div>
            </div>

            <h2
              className={
                !visibleAmounts[visibilityKey]
                  ? "stat-value-blurred"
                  : ""
              }
            >
              {loading
                ? <span className="skeleton skeleton-value" />
                : summary
                  ? money(value, visibilityKey)
                  : "—"}
            </h2>

            <p>{note}</p>
          </article>
        ))}
      </section>

      {/* Cash flow and monthly position */}
      <section className="dashboard-grid">

        <article className="panel cashflow-panel">
          <div className="panel-heading">
            <div>
              <h3>Cash flow</h3>
              <p>Money coming in and going out</p>
            </div>

            <span className="period-label">
              {periodLabel}
            </span>
          </div>

          <div className="dashboard-cashflow">

            <div className="cashflow-row">
              <div className="cashflow-label">
                <span>Income</span>
                <strong>
                  {loading
                    ? <span className="skeleton skeleton-small-value" />
                    : summary
                      ? money(income)
                      : "—"}
                </strong>
              </div>
            </div>

            <div className="cashflow-row">
              <div className="cashflow-label">
                <span>Expenses</span>
                <strong>
                  {loading
                    ? <span className="skeleton skeleton-small-value" />
                    : summary
                      ? money(expenses)
                      : "—"}
                </strong>
              </div>
            </div>

            <div className={`cashflow-net ${
              netCashflow > 0
                ? "net-positive"
                : netCashflow < 0
                  ? "net-negative"
                  : ""
            }`}>
              <span>Net cash flow</span>

              <strong>
                {loading
                  ? <span className="skeleton skeleton-small-value" />
                  : summary
                    ? money(netCashflow)
                    : "—"}
              </strong>
            </div>
          </div>
        </article>

        <article className="panel monthly-insight-panel">
          <div className="panel-heading">
            <div>
              <h3>Monthly position</h3>
              <p>Your income and spending balance</p>
            </div>

            <Activity size={19} className="insight-icon" />
          </div>

          {loading ? (
            <div className="monthly-insight-loading">
              <span className="skeleton skeleton-value" />
              <span className="skeleton skeleton-line skeleton-line-wide" />
            </div>
          ) : summary ? (
            <>
              <div className={`monthly-net-value ${
                netCashflow >= 0 ? "net-positive" : "net-negative"
              }`}>
                {money(netCashflow)}
              </div>

              <span className="monthly-net-caption">
                {netCashflow > 0
                  ? "You earned more than you spent this month."
                  : netCashflow < 0
                    ? "Your spending exceeded your income this month."
                    : "Your income and expenses are currently balanced."}
              </span>

              <div className="monthly-ratio">
                <div>
                  <span>Expenses vs income</span>
                  <strong>
                    {expenseRatio === null
                      ? "—"
                      : `${expenseRatio}%`}
                  </strong>
                </div>

              </div>
            </>
          ) : (
            <p className="insight-empty">
              Your monthly position will appear when financial data is
              available.
            </p>
          )}
        </article>

      </section>

      {/* Recent transactions */}
      <section className="panel transactions-panel">
        <div className="panel-heading">
          <div>
            <h3>Recent transactions</h3>
            <p>Your latest financial activity</p>
          </div>

          <button
            type="button"
            className="text-button"
            onClick={() => navigate("/transactions")}
          >
            View all <ArrowRight size={16} />
          </button>
        </div>

        {loading ? (
          <div
            className="dashboard-skeleton-list"
            aria-label="Loading transactions"
          >
            {[1, 2, 3].map((item) => (
              <div className="dashboard-skeleton-row" key={item}>
                <span className="skeleton skeleton-avatar" />

                <div className="dashboard-skeleton-details">
                  <span className="skeleton skeleton-line skeleton-line-wide" />
                  <span className="skeleton skeleton-line skeleton-line-short" />
                </div>

                <span className="skeleton skeleton-status" />
              </div>
            ))}
          </div>
        ) : recentTransactions.length > 0 ? (
          <div className="dashboard-recent-list">
            {recentTransactions.map((entry, index) => {
              const status = String(entry.status || "unknown")
                .toLowerCase()
                .replace(/[^a-z0-9_-]/g, "");

              const direction = ["inflow", "outflow", "transfer"].includes(
                entry.direction
              ) ? entry.direction : "unknown";

              const hasAmount =
                entry.amount_minor !== null &&
                entry.amount_minor !== undefined &&
                Number.isFinite(Number(entry.amount_minor));

              return (
                <div
                  className="dashboard-recent-item"
                  key={entry.id || entry.reference || index}
                >
                  <div className={`dashboard-recent-icon ${direction}`}>
                    {direction === "inflow" ? (
                      <TrendingUp size={19} />
                    ) : direction === "outflow" ? (
                      <TrendingDown size={19} />
                    ) : (
                      <CircleDollarSign size={19} />
                    )}
                  </div>

                  <div className="dashboard-recent-details">
                    <strong>
                      {entry.description ||
                        entry.reference ||
                        "Untitled transaction"}
                    </strong>

                    <span>
                      {formatDate(entry.transaction_date, timezone)}
                      {entry.reference
                        ? ` · ${entry.reference}`
                        : ""}
                    </span>
                  </div>

                  <span
                    className={`transaction-status status-${status}`}
                  >
                    {formatStatus(entry.status)}
                  </span>

                  <div className={`dashboard-recent-amount ${direction}`}>
                    <strong>
                      {hasAmount
                        ? `${direction === "inflow" ? "+" : direction === "outflow" ? "−" : ""}${formatMoney(
                            Math.abs(Number(entry.amount_minor)),
                            currency,
                            minorUnit
                          )}`
                        : "—"}
                    </strong>
                    <span>
                      {direction === "inflow"
                        ? "Received"
                        : direction === "outflow"
                          ? "Spent"
                          : direction === "transfer"
                            ? "Transfer"
                            : "Amount unavailable"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-transactions">
            <div className="empty-icon">
              <Wallet size={25} />
            </div>

            <strong>No transactions yet</strong>

            <span>
              Your transactions will appear here when you start
              recording them.
            </span>

            <button
              type="button"
              className="text-button"
              onClick={() => navigate("/transactions")}
            >
              Record your first transaction
              <ArrowRight size={16} />
            </button>
          </div>
        )}
      </section>

      <div className="dashboard-footnote">
        <span className="status-dot" />

        {loading
          ? <span className="skeleton skeleton-footnote" />
          : "Your financial overview is based on your posted accounting entries."}
      </div>
    </div>
  );
}
