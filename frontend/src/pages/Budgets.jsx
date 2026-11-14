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
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showCategoryForm, setShowCategoryForm] = useState(false);
  const [showBudgetForm, setShowBudgetForm] = useState(false);
  const [categoryName, setCategoryName] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState(currentMonthPeriod);

  const loadData = useCallback(async (background = false) => {
    if (!workspaceId) {
      setCategories([]);
      setBudgets([]);
      setLoading(false);
      return;
    }

    if (background) setRefreshing(true);
    else setLoading(true);

    setError("");

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
      setError(err.message || "Unable to load budgets.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const totals = useMemo(() => budgets.reduce((result, budget) => {
    result.allocated += Number(budget.amount_minor || 0);
    result.spent += Number(budget.actual_minor || 0);
    result.remaining += Number(budget.remaining_minor || 0);
    return result;
  }, { allocated: 0, spent: 0, remaining: 0 }), [budgets]);

  async function handleCreateCategory(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!categoryName.trim()) {
      setError("Enter a category name.");
      return;
    }

    setSaving(true);

    try {
      const result = await financeApi.createExpenseCategory({
        workspace_id: workspaceId,
        name: categoryName.trim(),
      });

      setCategoryName("");
      setShowCategoryForm(false);
      setSuccess(result.message || "Category created successfully.");
      await loadData(true);
    } catch (err) {
      setError(err.message || "Could not create category.");
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateBudget(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!categoryId) {
      setError("Create or select an expense category first.");
      return;
    }

    if (!period.start || !period.end || period.end < period.start) {
      setError("Choose a valid budget period.");
      return;
    }

    let amountMinor;

    try {
      amountMinor = toMinorUnits(amount, currency);
    } catch (err) {
      setError(err.message);
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
      setShowBudgetForm(false);
      setSuccess(result.message || "Budget created successfully.");
      await loadData(true);
    } catch (err) {
      setError(err.message || "Could not create budget.");
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
      <div className="accounts-heading">
        <div>
          <h1>Budgets</h1>
          <p>Plan your spending and keep track of where your money goes.</p>
        </div>

        <div className="accounts-actions">
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
              setError("");
              setShowBudgetForm((value) => !value);
              setShowCategoryForm(false);
            }}
          >
            <Plus size={17} />
            New budget
          </button>
        </div>
      </div>

      {error && (
        <div className="accounts-message error">
          <AlertCircle size={17} />
          {error}
        </div>
      )}

      {success && (
        <div className="accounts-message success">
          <CheckCircle2 size={17} />
          {success}
        </div>
      )}

      <div className="accounts-summary budget-summary">
        <div className="accounts-summary-card">
          <span><Wallet size={17} /> Total allocated</span>
          <strong>{formatMoney(totals.allocated, currency)}</strong>
        </div>
        <div className="accounts-summary-card">
          <span><Tags size={17} /> Total spent</span>
          <strong>{formatMoney(totals.spent, currency)}</strong>
        </div>
        <div className="accounts-summary-card">
          <span><CheckCircle2 size={17} /> Remaining</span>
          <strong>{formatMoney(totals.remaining, currency)}</strong>
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
              setError("");
              setShowCategoryForm((value) => !value);
              setShowBudgetForm(false);
            }}
          >
            <Plus size={16} />
            Add category
          </button>
        </div>

        {loading ? (
          <div className="budget-empty-state">Loading your budgets...</div>
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
        ) : (
          <div className="budget-grid">
            {budgets.map((budget) => {
              const percentage = Math.max(
                0,
                Number(budget.usage_percentage || 0)
              );
              const over = budget.is_over_budget;
              const progress = Math.min(percentage, 100);

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
                    <span className={`budget-status ${over ? "over" : ""}`}>
                      {over ? "Over budget" : "On track"}
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
                      className={`budget-progress-fill ${over ? "over" : ""}`}
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
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
