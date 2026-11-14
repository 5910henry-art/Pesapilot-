import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Wallet, TrendingUp, TrendingDown, PiggyBank,
  ArrowDownLeft, ArrowUpRight, Plus, ArrowRight,
  HandCoins, CircleDollarSign, AlertCircle,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { financeApi } from "../api/client";

function formatMoney(minor, currency, minorUnit = 2) {
  const amount = Number(minor || 0) / (10 ** minorUnit);

  try {
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency,
      minimumFractionDigits: minorUnit,
      maximumFractionDigits: minorUnit,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(minorUnit)}`;
  }
}

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

export default function Dashboard() {
  const { profile, activeWorkspace } = useAuth();
  const navigate = useNavigate();

  const workspaceId = activeWorkspace?.id;
  const fallbackCurrency =
    activeWorkspace?.currency || profile?.preferred_currency || "KES";

  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadDashboard() {
      if (!workspaceId) {
        setSummary(null);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError("");

      try {
        const result = await financeApi.getDashboardSummary(workspaceId);

        if (!cancelled) {
          setSummary(result);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Could not load your financial overview.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadDashboard();

    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const currency = summary?.currency || fallbackCurrency;
  const minorUnit = summary?.minor_unit ?? 2;
  const totals = summary?.summary || {};

  const money = (value) => formatMoney(value, currency, minorUnit);

  const firstName = profile?.first_name || "there";
  const [greeting] = useState(() => {
    const hour = new Date().getHours();

    return hour < 12
      ? "Good morning"
      : hour < 17
        ? "Good afternoon"
        : "Good evening";
  });

  const stats = [
    {
      title: "Total balance",
      amount: money(totals.total_assets_minor),
      note: "Across your asset accounts",
      icon: Wallet,
      color: "green",
    },
    {
      title: "Total income",
      amount: money(totals.monthly_income_minor),
      note: "This month",
      icon: TrendingUp,
      color: "blue",
    },
    {
      title: "Total expenses",
      amount: money(totals.monthly_expenses_minor),
      note: "This month",
      icon: TrendingDown,
      color: "orange",
    },
    {
      title: "Total savings",
      amount: money(totals.total_savings_minor),
      note: "Across savings accounts",
      icon: PiggyBank,
      color: "purple",
    },
  ];

  const income = Number(totals.monthly_income_minor || 0);
  const expenses = Number(totals.monthly_expenses_minor || 0);
  const largestFlow = Math.max(income, expenses, 1);
  const recentTransactions = summary?.recent_transactions || [];

  return (
    <div className="dashboard">
      <section className="welcome-section">
        <div>
          <div className="eyebrow">YOUR FINANCIAL COMMAND CENTRE</div>
          <h1>{greeting}, {firstName} <span>👋</span></h1>
          <p>Here's an overview of your financial life.</p>
        </div>

        <button
          className="primary-button"
          onClick={() => navigate("/transactions")}
        >
          <Plus size={18} />
          Add transaction
        </button>
      </section>

      {error && (
        <div className="dashboard-alert" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
          <button onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      )}

      <section className="stats-grid">
        {stats.map(({ title, amount, note, icon: Icon, color }) => (
          <article className="stat-card" key={title}>
            <div className="stat-top">
              <span>{title}</span>
              <div className={`stat-icon ${color}`}>
                <Icon size={20} />
              </div>
            </div>
            <h2>{loading ? <span className="skeleton skeleton-value" /> : amount}</h2>
            <p>{note}</p>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel cashflow-panel">
          <div className="panel-heading">
            <div>
              <h3>Cash flow</h3>
              <p>Money coming in and going out this month</p>
            </div>
            <span className="period-label">This month</span>
          </div>

          <div className="dashboard-cashflow">
            <div className="cashflow-row">
              <div className="cashflow-label">
                <span>Income</span>
                <strong>{loading ? <span className="skeleton skeleton-value" /> : money(income)}</strong>
              </div>
              <div className="cashflow-track">
                <div
                  className={loading ? "skeleton skeleton-bar" : "cashflow-fill income-fill"}
                  style={loading ? undefined : { width: `${(income / largestFlow) * 100}%` }}
                />
              </div>
            </div>

            <div className="cashflow-row">
              <div className="cashflow-label">
                <span>Expenses</span>
                <strong>{loading ? <span className="skeleton skeleton-value" /> : money(expenses)}</strong>
              </div>
              <div className="cashflow-track">
                <div
                  className={loading ? "skeleton skeleton-bar" : "cashflow-fill expense-fill"}
                  style={loading ? undefined : { width: `${(expenses / largestFlow) * 100}%` }}
                />
              </div>
            </div>

            <div className="cashflow-net">
              <span>Net cash flow</span>
              <strong>{loading ? <span className="skeleton skeleton-value" /> : money(income - expenses)}</strong>
            </div>
          </div>
        </article>

        <article className="panel">
          <div className="panel-heading">
            <div>
              <h3>Quick overview</h3>
              <p>Your financial activity</p>
            </div>
          </div>

          <div className="overview-list">
            <div className="overview-item">
              <div className="overview-icon income">
                <ArrowDownLeft size={19} />
              </div>
              <div className="overview-text">
                <strong>Income</strong>
                <span>Money received this month</span>
              </div>
              <strong>{loading ? <span className="skeleton skeleton-small-value" /> : money(income)}</strong>
            </div>

            <div className="overview-item">
              <div className="overview-icon expense">
                <ArrowUpRight size={19} />
              </div>
              <div className="overview-text">
                <strong>Expenses</strong>
                <span>Money spent this month</span>
              </div>
              <strong>{loading ? <span className="skeleton skeleton-small-value" /> : money(expenses)}</strong>
            </div>

            <div className="overview-item">
              <div className="overview-icon savings">
                <PiggyBank size={19} />
              </div>
              <div className="overview-text">
                <strong>Savings</strong>
                <span>Money set aside</span>
              </div>
              <strong>
                {loading ? <span className="skeleton skeleton-small-value" /> : money(totals.total_savings_minor)}
              </strong>
            </div>

            <div className="overview-item">
              <div className="overview-icon loans">
                <HandCoins size={19} />
              </div>
              <div className="overview-text">
                <strong>Loans</strong>
                <span>Lending module not connected</span>
              </div>
              <strong>—</strong>
            </div>
          </div>
        </article>
      </section>

      <section className="panel transactions-panel">
        <div className="panel-heading">
          <div>
            <h3>Recent transactions</h3>
            <p>Your latest financial activity</p>
          </div>
          <button
            className="text-button"
            onClick={() => navigate("/transactions")}
          >
            View all <ArrowRight size={16} />
          </button>
        </div>

        {loading ? (
          <div className="dashboard-skeleton-list" aria-label="Loading transactions">
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
            {recentTransactions.map((entry) => (
              <div className="dashboard-recent-item" key={entry.id}>
                <div className="dashboard-recent-icon">
                  <CircleDollarSign size={19} />
                </div>
                <div className="dashboard-recent-details">
                  <strong>{entry.description || entry.reference}</strong>
                  <span>
                    {formatDate(entry.transaction_date)}
                    {entry.reference ? ` · ${entry.reference}` : ""}
                  </span>
                </div>
                <span className={`transaction-status status-${entry.status}`}>
                  {entry.status}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-transactions">
            <div className="empty-icon">
              <Wallet size={25} />
            </div>
            <strong>No transactions yet</strong>
            <span>
              Your transactions will appear here when you start recording them.
            </span>
            <button
              className="text-button"
              onClick={() => navigate("/transactions")}
            >
              Record your first transaction <ArrowRight size={16} />
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
