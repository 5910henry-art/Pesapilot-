import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Plus,
  RefreshCw,
  Tags,
  Wallet,
} from "lucide-react";

import { useAuth } from "../context/useAuth";
import { financeApi } from "../api/client";
import { Skeleton } from "../components/Skeleton";

const MINOR_UNITS = {
  BIF: 0, CLP: 0, DJF: 0, GNF: 0, JPY: 0, KMF: 0,
  KRW: 0, RWF: 0, UGX: 0, VND: 0, VUV: 0, XAF: 0,
  XOF: 0, XPF: 0,
};

function currencyDigits(currency) {
  return MINOR_UNITS[currency] ?? 2;
}

function formatMoney(minor, currency) {
  const digits = currencyDigits(currency);
  const amount = Number(minor || 0) / (10 ** digits);

  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
}

function toMinorUnits(value, currency) {
  const digits = currencyDigits(currency);
  const input = String(value).trim();

  if (!/^\d+(\.\d+)?$/.test(input)) {
    throw new Error("Enter a valid positive amount.");
  }

  const [whole, fraction = ""] = input.split(".");

  if (fraction.length > digits) {
    throw new Error(`This currency supports at most ${digits} decimal places.`);
  }

  const minor = BigInt(whole) * (10n ** BigInt(digits))
    + BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");

  if (minor <= 0n || minor > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("Enter an amount within the supported range.");
  }

  return Number(minor);
}

function localDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentMonthPeriod() {
  const now = new Date();

  return {
    start: localDate(new Date(now.getFullYear(), now.getMonth(), 1)),
    end: localDate(new Date(now.getFullYear(), now.getMonth() + 1, 0)),
  };
}

const SUGGESTED_CATEGORIES = [
  "Food & Groceries",
  "Rent & Housing",
  "Transport",
  "Utilities",
  "Communication",
  "Healthcare",
  "Education",
  "Clothing",
  "Family & Dependants",
  "Entertainment",
  "Dining Out",
  "Savings",
  "Debt Repayment",
  "Personal Care",
  "Miscellaneous",
];

export default function Budgets() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;

  const currency =
    activeWorkspace?.functional_currency ||
    activeWorkspace?.currency_code ||
    activeWorkspace?.currency?.code ||
    "KES";

  const [categories, setCategories] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [categoryMessage, setCategoryMessage] = useState(null);
  const [budgetMessage, setBudgetMessage] = useState(null);
  const [showCategoryForm, setShowCategoryForm] = useState(false);
  const [showBudgetForm, setShowBudgetForm] = useState(false);
  const [categoryName, setCategoryName] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState(currentMonthPeriod);
  const [periodFilter, setPeriodFilter] = useState("all");
  const [filterMonth, setFilterMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });

  const filteredBudgets = useMemo(() => {
    if (periodFilter === "all") return budgets;

    const [year, month] = filterMonth.split("-").map(Number);
    if (!year || !month) return budgets;

    const start = `${filterMonth}-01`;
    const end = localDate(new Date(year, month, 0));

    return budgets.filter((budget) =>
      budget.period_start <= end && budget.period_end >= start
    );
  }, [budgets, periodFilter, filterMonth]);

  const loadData = useCallback(async (background = false) => {
    if (!workspaceId) {
      setCategories([]);
      setBudgets([]);
      setLoading(false);
      return;
    }

    if (background) setRefreshing(true);
    else setLoading(true);

    setLoadError("");

    try {
      const [categoryResult, budgetResult] = await Promise.all([
        financeApi.listExpenseCategories(workspaceId),
        financeApi.listBudgets(workspaceId),
      ]);

      const nextCategories = categoryResult.categories || [];

      setCategories(nextCategories.filter(
        (category) => category.is_active && category.ledger_account?.id
      ));
      setBudgets(budgetResult.budgets || []);

      setCategoryId((current) =>
        nextCategories.some((item) => item.id === current && item.is_active)
          ? current
          : nextCategories.find((item) => item.is_active)?.id || ""
      );
    } catch (err) {
      setLoadError(err.message || "Unable to load budgets.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const totals = useMemo(() => filteredBudgets.reduce((result, budget) => {
    result.allocated += Number(budget.amount_minor || 0);
    result.spent += Number(budget.actual_minor || 0);
    result.remaining += Number(budget.remaining_minor || 0);
    return result;
  }, { allocated: 0, spent: 0, remaining: 0 }), [filteredBudgets]);

  async function handleCreateCategory(event) {
    event.preventDefault();
    setCategoryMessage(null);

    if (!categoryName.trim()) {
      setCategoryMessage({ type: "error", text: "Enter a category name." });
      return;
    }

    setSaving(true);

    try {
      const result = await financeApi.createExpenseCategory({
        workspace_id: workspaceId,
        name: categoryName.trim(),
      });

      setCategoryName("");
      setCategoryMessage({ type: "success", text: result.message || "Category created successfully." });
      await loadData(true);
    } catch (err) {
      setCategoryMessage({ type: "error", text: err.message || "Could not create category." });
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateBudget(event) {
    event.preventDefault();
    setBudgetMessage(null);

    if (!categoryId) {
      setBudgetMessage({ type: "error", text: "Create or select an expense category first." });
      return;
    }

    if (!period.start || !period.end || period.end < period.start) {
      setBudgetMessage({ type: "error", text: "Choose a valid budget period." });
      return;
    }

    let amountMinor;

    try {
      amountMinor = toMinorUnits(amount, currency);
    } catch (err) {
      setBudgetMessage({ type: "error", text: err.message });
      return;
    }

    setSaving(true);

    try {
      const result = await financeApi.createBudget({
        workspace_id: workspaceId,
        category_id: categoryId,
        amount_minor: amountMinor,
        period_start: period.start,
        period_end: period.end,
      });

      setAmount("");
      setBudgetMessage({ type: "success", text: result.message || "Budget created successfully." });
      await loadData(true);
    } catch (err) {
      setBudgetMessage({ type: "error", text: err.message || "Could not create budget." });
    } finally {
      setSaving(false);
    }
  }

  if (!workspaceId) {
    return (
      <section className="budgets-page">
        <div className="panel">
          <p>Select a workspace to manage budgets.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="budgets-page">
      <div className="budgets-hero">
        <div className="budgets-hero-copy">
          <div className="budgets-hero-kicker">
            <Wallet size={14} />
            SPENDING PLAN
          </div>
          <h1>Give every shilling a purpose.</h1>
          <p>
            Set clear spending limits, follow your progress and stay in control
            of your money throughout the month.
          </p>
          <div className="budgets-hero-meta">
            <span><Tags size={15} /> Category-based planning</span>
            <span><CheckCircle2 size={15} /> Track spending progress</span>
          </div>
        </div>

        <div className="budgets-hero-side">
          <div className="budgets-hero-art" aria-hidden="true">
            <Wallet size={39} strokeWidth={1.5} />
          </div>
          <div className="budgets-hero-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={() => loadData(true)}
              disabled={refreshing}
            >
              <RefreshCw size={16} className={refreshing ? "spin" : ""} />
              Refresh
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                setBudgetMessage(null);
                setShowBudgetForm((value) => !value);
                setShowCategoryForm(false);
              }}
            >
              <Plus size={17} />
              New budget
            </button>
          </div>
        </div>
      </div>

      {loadError && (
        <div className="accounts-message error" role="alert">
          <AlertCircle size={17} />
          {loadError}
        </div>
      )}

      <div className="accounts-summary budget-summary">
        <div className="accounts-summary-card">
          <span><Wallet size={17} /> Total allocated</span>
          <strong>{loading ? <Skeleton width="115px" height={23} /> : formatMoney(totals.allocated, currency)}</strong>
        </div>
        <div className="accounts-summary-card">
          <span><Tags size={17} /> Total spent</span>
          <strong>{loading ? <Skeleton width="115px" height={23} /> : formatMoney(totals.spent, currency)}</strong>
        </div>
        <div className="accounts-summary-card">
          <span><CheckCircle2 size={17} /> Remaining</span>
          <strong>{loading ? <Skeleton width="115px" height={23} /> : formatMoney(totals.remaining, currency)}</strong>
        </div>
      </div>

      {showCategoryForm && (
        <div className="panel budget-form-panel">
          <div className="panel-heading">
            <div>
              <h2>Create expense category</h2>
              <p>Give your spending a category, such as Food or Transport.</p>
            </div>
          </div>

          <form className="account-form" onSubmit={handleCreateCategory}>
            <div className="budget-category-suggestions">
              <span className="budget-suggestions-label">
                Suggested categories
              </span>
              <div className="budget-suggestion-list">
                {SUGGESTED_CATEGORIES.map((name) => {
                  const exists = categories.some(
                    (category) =>
                      category.name.trim().toLowerCase() === name.toLowerCase()
                  );

                  return (
                    <button
                      key={name}
                      type="button"
                      className={`budget-suggestion-chip${exists ? " added" : ""}`}
                      disabled={exists || saving}
                      onClick={() => setCategoryName(name)}
                    >
                      {name}
                      {exists && <span>Added</span>}
                    </button>
                  );
                })}
              </div>
            </div>

            <label>
              Category name
              <input
                value={categoryName}
                onChange={(event) => setCategoryName(event.target.value)}
                placeholder="e.g. Food, Transport, Rent"
                maxLength={100}
                required
              />
            </label>
            <div className="account-form-actions">
              <button className="primary-button" disabled={saving}>
                {saving ? "Creating..." : "Create category"}
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setShowCategoryForm(false)}
              >
                Cancel
              </button>
            </div>
            {categoryMessage && (
              <div className={`accounts-message ${categoryMessage.type}`} role={categoryMessage.type === "error" ? "alert" : "status"}>
                {categoryMessage.type === "error" ? <AlertCircle size={17} /> : <CheckCircle2 size={17} />}
                {categoryMessage.text}
              </div>
            )}
          </form>
        </div>
      )}

      {showBudgetForm && (
        <div className="panel budget-form-panel">
          <div className="panel-heading">
            <div>
              <h2>Create a budget</h2>
              <p>Set a spending limit for a category and date range.</p>
            </div>
          </div>

          <form className="account-form" onSubmit={handleCreateBudget}>
            <label>
              Expense category
              <select
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
                required
              >
                <option value="">Select a category</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Budget amount ({currency})
              <input
                type="number"
                min="0"
                step={currencyDigits(currency) === 0 ? "1" : "0.01"}
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="e.g. 15000"
                required
              />
            </label>

            <label>
              Start date
              <input
                type="date"
                value={period.start}
                onChange={(event) =>
                  setPeriod((current) => ({ ...current, start: event.target.value }))
                }
                required
              />
            </label>

            <label>
              End date
              <input
                type="date"
                value={period.end}
                onChange={(event) =>
                  setPeriod((current) => ({ ...current, end: event.target.value }))
                }
                required
              />
            </label>

            {!categories.length && (
              <p className="budget-form-hint">
                Create an expense category before setting a budget.
              </p>
            )}

            {budgetMessage && (
              <div className={`accounts-message ${budgetMessage.type}`} role={budgetMessage.type === "error" ? "alert" : "status"}>
                {budgetMessage.type === "error" ? <AlertCircle size={17} /> : <CheckCircle2 size={17} />}
                {budgetMessage.text}
              </div>
            )}
            <div className="account-form-actions">
              <button
                className="primary-button"
                disabled={saving || !categories.length}
              >
                {saving ? "Creating..." : "Create budget"}
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setShowBudgetForm(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="panel budget-list-panel">
        <div className="panel-heading">
          <div>
            <h2>Your budgets</h2>
            <p>Monitor spending against your category limits.</p>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setCategoryMessage(null);
              setShowCategoryForm((value) => !value);
              setShowBudgetForm(false);
            }}
          >
            <Plus size={16} />
            Add category
          </button>
        </div>

        <div className="budget-filter-row">
          <label>
            Period
            <select
              value={periodFilter}
              onChange={(event) => setPeriodFilter(event.target.value)}
            >
              <option value="all">All periods</option>
              <option value="month">Selected month</option>
            </select>
          </label>
          {periodFilter === "month" && (
            <label>
              Choose month
              <input
                type="month"
                value={filterMonth}
                onChange={(event) => setFilterMonth(event.target.value)}
              />
            </label>
          )}
          <span>{filteredBudgets.length} budget{filteredBudgets.length === 1 ? "" : "s"}</span>
        </div>

        {loading ? (
          <div className="budget-grid budget-skeleton-grid" aria-hidden="true">
            {Array.from({ length: 2 }, (_, index) => (
              <article className="budget-card budget-skeleton-card" key={index}>
                <div className="budget-card-heading">
                  <div className="budget-skeleton-heading">
                    <Skeleton width="58%" height={16} />
                    <Skeleton width="78%" height={11} />
                  </div>
                  <Skeleton width={72} height={22} radius={20} />
                </div>
                <div className="budget-card-amount">
                  <Skeleton width="42%" height={22} />
                  <Skeleton width="32%" height={13} />
                </div>
                <Skeleton width="100%" height={9} radius={20} />
                <div className="budget-card-footer">
                  <Skeleton width="25%" height={12} />
                  <Skeleton width="38%" height={14} />
                </div>
                <div className="budget-daily-guidance budget-skeleton-guidance">
                  <div>
                    <Skeleton width={150} height={11} />
                    <Skeleton width={95} height={16} />
                  </div>
                  <Skeleton width={75} height={10} />
                </div>
              </article>
            ))}
          </div>
        ) : budgets.length === 0 ? (
          <div className="budget-empty-state">
            <CalendarDays size={34} />
            <h3>No budgets yet</h3>
            <p>Create an expense category, then set your first budget.</p>
            <button
              className="primary-button"
              type="button"
              onClick={() => setShowCategoryForm(true)}
            >
              <Plus size={16} />
              Create category
            </button>
          </div>
        ) : filteredBudgets.length === 0 ? (
          <div className="budget-empty-state">
            <CalendarDays size={34} />
            <h3>No budgets for this period</h3>
            <p>Try another month or switch to all periods.</p>
          </div>
        ) : (
          <div className="budget-grid">
            {filteredBudgets.map((budget) => {
              const percentage = Math.max(
                0,
                Number(budget.usage_percentage || 0)
              );
              const over = budget.is_over_budget;
              const nearLimit = !over && percentage >= 80;
              const progress = Math.min(percentage, 100);
              const statusClass = over ? "over" : nearLimit ? "near" : "";

              const today = localDate(new Date());
              const startsTodayOrEarlier = budget.period_start <= today;
              const endsTodayOrLater = budget.period_end >= today;
              const activeNow = startsTodayOrEarlier && endsTodayOrLater;
              const remainingDays = activeNow
                ? Math.max(
                    1,
                    Math.ceil(
                      (new Date(`${budget.period_end}T00:00:00`) -
                        new Date(`${today}T00:00:00`)) / 86400000
                    ) + 1
                  )
                : 0;
              const dailyAllowance = activeNow && Number(budget.remaining_minor) > 0
                ? Number(budget.remaining_minor) / remainingDays
                : 0;

              return (
                <article className="budget-card" key={budget.id}>
                  <div className="budget-card-heading">
                    <div>
                      <h3>{budget.category?.name || "Expense category"}</h3>
                      <span className="budget-period">
                        <CalendarDays size={14} />
                        {budget.period_start} – {budget.period_end}
                      </span>
                    </div>
                    <span className={`budget-status ${statusClass}`}>
                      {over ? "Over budget" : nearLimit ? "Near limit" : "On track"}
                    </span>
                  </div>

                  <div className="budget-card-amount">
                    <strong>{formatMoney(budget.actual_minor, budget.currency_code)}</strong>
                    <span>of {formatMoney(budget.amount_minor, budget.currency_code)}</span>
                  </div>

                  <div
                    className="budget-progress"
                    role="progressbar"
                    aria-valuenow={Math.round(percentage)}
                    aria-valuemin="0"
                    aria-valuemax="100"
                    aria-label={`${budget.category?.name || "Budget"} spending`}
                  >
                    <div
                      className={`budget-progress-fill ${statusClass}`}
                      style={{ width: `${progress}%` }}
                    />
                  </div>

                  <div className="budget-card-footer">
                    <span>{percentage.toFixed(1)}% used</span>
                    <strong className={over ? "budget-over-amount" : ""}>
                      {formatMoney(budget.remaining_minor, budget.currency_code)}
                      {" "}{over ? "over" : "left"}
                    </strong>
                  </div>

                  {activeNow && (
                    <div className="budget-daily-guidance">
                      <div>
                        <span>Suggested daily spending limit</span>
                        <strong>
                          {formatMoney(Math.floor(dailyAllowance), budget.currency_code)}
                        </strong>
                      </div>
                      <small>
                        {remainingDays} day{remainingDays === 1 ? "" : "s"} remaining
                      </small>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
