import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDownLeft, ArrowUpRight, ArrowLeftRight,
  Plus, Wallet, AlertCircle,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { useToast } from "../context/ToastContext";
import { financeApi } from "../api/client";
import { Skeleton } from "./Skeleton";

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

function convertMinorUnits(amountMinor, rate, sourceCurrency, targetCurrency) {
  const sourceDigits = MINOR_UNITS[sourceCurrency] ?? 2;
  const targetDigits = MINOR_UNITS[targetCurrency] ?? 2;
  const rateText = String(rate);
  const [whole, fraction = ""] = rateText.split(".");
  const rateScale = 10n ** BigInt(fraction.length);
  const rateValue = BigInt(whole + fraction);

  let numerator = BigInt(amountMinor) * rateValue;
  let denominator = rateScale;
  const difference = targetDigits - sourceDigits;

  if (difference >= 0) {
    numerator *= 10n ** BigInt(difference);
  } else {
    denominator *= 10n ** BigInt(-difference);
  }

  const result = (numerator + denominator / 2n) / denominator;

  return result > 0n && result <= BigInt(Number.MAX_SAFE_INTEGER)
    ? Number(result)
    : null;
}

export default function TransactionEntryForm({
  allowedTypes = ["income", "expense", "transfer", "opening"],
  initialType = "income",
  title = "Record a transaction",
  description = "Entries are posted to your double-entry ledger.",
  onRecorded,
  footerAction = null,
  collapsible = false,
  initiallyCollapsed = false,
}) {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;
  const workspaceCurrency = activeWorkspace?.currency || "KES";

  const availableTypes = useMemo(
    () => TYPES.filter((type) => allowedTypes.includes(type.value)),
    [allowedTypes]
  );

  const startingType = availableTypes.some((type) => type.value === initialType)
    ? initialType
    : availableTypes[0]?.value || "income";

  const {
    accounts,
    balances: ledgerAccounts,
    loading: accountsLoading,
    refreshAccounts,
  } = useAccounts();

  const [expenseCategories, setExpenseCategories] = useState([]);
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const { showToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [isExpanded, setIsExpanded] = useState(!initiallyCollapsed);

  const [form, setForm] = useState({
    type: startingType,
    accountId: "",
    secondAccountId: "",
    amount: "",
    description: "",
    date: localDateValue(),
    exchangeRate: "",
    categoryId: "",
  });

  const loading = accountsLoading || (form.type === "expense" && categoriesLoading);

  const loadData = useCallback(async () => {
    if (!workspaceId) {
      setExpenseCategories([]);
      setCategoriesLoading(false);
      return;
    }

    setCategoriesLoading(true);
    setError("");

    try {
      const categoryData = await financeApi.listExpenseCategories(workspaceId);

      setExpenseCategories(
        (categoryData.categories || []).filter(
          (category) => category.is_active && category.ledger_account?.id
        )
      );
    } catch (err) {
      showToast(err.message || "Could not load expense categories.", { type: "error" });
    } finally {
      setCategoriesLoading(false);
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
    income: ledgerAccounts.find(
      (item) => item.code === "4000" && item.is_active
    ),
    equity: ledgerAccounts.find(
      (item) => item.code === "3000" && item.is_active
    ),
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

  function changeType(type) {
    setError("");

    setForm((current) => ({
      ...current,
      type,
      amount: "",
      description: "",
      exchangeRate: "",
      categoryId: "",
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!workspaceId) {
      setError("No active workspace is available.");
      return;
    }

    if (!selectedAccount) {
      setError("Create an active financial account first.");
      return;
    }

    if (form.type === "expense" && !selectedExpenseCategory) {
      setError(
        "Create or select an active expense category before recording this expense."
      );
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
        (digits === 0
          ? " (whole numbers only)."
          : ` (up to ${digits} decimal places).`)
      );
      return;
    }

    if (
      needsRate &&
      form.type !== "transfer" &&
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

    const workspaceAmountMinor = needsRate && form.type !== "transfer"
      ? convertMinorUnits(
          amountMinor,
          form.exchangeRate,
          selectedAccount.currency,
          workspaceCurrency
        )
      : amountMinor;

    if (workspaceAmountMinor === null) {
      setError("The converted amount is outside the supported range.");
      return;
    }

    const line = (ledgerId, side, rate, amount = amountMinor) => ({
      ledger_account_id: ledgerId,
      debit_minor: side === "debit" ? amount : 0,
      credit_minor: side === "credit" ? amount : 0,
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
        line(
          systemAccounts.income.ledger_account_id,
          "credit",
          null,
          workspaceAmountMinor
        ),
      ];
    } else if (form.type === "expense") {
      if (!selectedExpenseCategory?.ledger_account?.id) {
        setError(
          "The selected expense category ledger is unavailable. Refresh and try again."
        );
        return;
      }

      lines = [
        line(
          selectedExpenseCategory.ledger_account.id,
          "debit",
          null,
          workspaceAmountMinor
        ),
        line(
          selectedAccount.ledger_account.id,
          "credit",
          needsRate ? form.exchangeRate : null
        ),
      ];
    } else if (form.type === "opening") {
      if (!systemAccounts.equity) {
        setError(
          "The Owner's Equity ledger account is unavailable. Refresh and try again."
        );
        return;
      }

      lines = [
        line(
          selectedAccount.ledger_account.id,
          "debit",
          needsRate ? form.exchangeRate : null
        ),
        line(
          systemAccounts.equity.ledger_account_id,
          "credit",
          null,
          workspaceAmountMinor
        ),
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
        ...(form.type === "expense"
          ? { category_id: effectiveCategoryId }
          : {}),
      });

      const entryId = created.entry?.id;

      if (!entryId) {
        throw new Error(
          "The draft was created but the server did not return its transaction ID."
        );
      }

      try {
        await financeApi.postEntry(entryId, workspaceId);

        showToast("Transaction recorded and posted successfully.", { type: "success" });

        setForm((current) => ({
          ...current,
          amount: "",
          description: "",
          exchangeRate: "",
          categoryId: "",
          date: localDateValue(),
        }));
      } catch (postError) {
        showToast(
          `Draft saved, but it was not posted: ${postError.message}`,
          { type: "warning", duration: 6500 }
        );
      }

      await refreshAccounts();
      if (onRecorded) await onRecorded();
    } catch (err) {
      showToast(err.message || "Transaction could not be recorded.", { type: "error" });
    } finally {
      setSaving(false);
    }
  }

  const amountDigits =
    MINOR_UNITS[selectedAccount?.currency || workspaceCurrency] ?? 2;

  const amountStep = amountDigits === 0
    ? "1"
    : `0.${"0".repeat(amountDigits - 1)}1`;

  return (
    <section className="panel transaction-form-panel">
      <div className="panel-heading">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        {collapsible && (
          <button
            className="secondary-button transaction-collapse-toggle"
            type="button"
            onClick={() => setIsExpanded((value) => !value)}
            aria-expanded={isExpanded}
          >
            {!isExpanded && <Plus size={16} />}
            {isExpanded ? "Hide form" : "Record transaction"}
          </button>
        )}
      </div>

      {collapsible && !isExpanded && (
        <div className="transaction-collapsed-actions">
          {footerAction}
        </div>
      )}

      {(!collapsible || isExpanded) && (
        <>
      {error && (
        <div className="accounts-message error-message" role="alert">
          <AlertCircle size={17} />{error}
        </div>
      )}

      {availableTypes.length > 1 && (
        <div
          className="transaction-type-tabs"
          role="group"
          aria-label="Transaction type"
        >
          {availableTypes.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              className={
                form.type === value
                  ? "transaction-type active"
                  : "transaction-type"
              }
              onClick={() => changeType(value)}
            >
              <Icon size={17} /><span>{label}</span>
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="transaction-form transaction-form-skeleton" aria-hidden="true">
          <div className="transaction-skeleton-field">
            <Skeleton width="105px" height={11} />
            <Skeleton height={43} radius={9} />
          </div>
          <div className="transaction-skeleton-field">
            <Skeleton width="75px" height={11} />
            <Skeleton height={43} radius={9} />
          </div>
          <div className="transaction-skeleton-field">
            <Skeleton width="90px" height={11} />
            <Skeleton height={43} radius={9} />
          </div>
          <div className="transaction-skeleton-field">
            <Skeleton width="65px" height={11} />
            <Skeleton height={43} radius={9} />
          </div>
          <div className="transaction-skeleton-field transaction-skeleton-wide">
            <Skeleton width="80px" height={11} />
            <Skeleton height={76} radius={9} />
          </div>
          <Skeleton width="145px" height={43} radius={9} />
        </div>
      ) : usableAccounts.length === 0 ? (
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
                <small>
                  Create an expense category in Budgets before recording an expense.
                </small>
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
                  .filter((account) => account.id !== effectiveAccountId)
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
              step={amountStep}
              placeholder="0.00"
              value={form.amount}
              onChange={(event) => setForm((current) => ({
                ...current,
                amount: event.target.value,
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
                  ...current,
                  exchangeRate: event.target.value,
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
                ...current,
                description: event.target.value,
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
                ...current,
                date: event.target.value,
              }))}
              required
            />
          </label>

          <div className="transaction-form-footer">
            <span>
              Workspace currency: <strong>{workspaceCurrency}</strong>
            </span>
            <div className="transaction-form-actions">
              {footerAction}
              <button
                className="primary-button"
                type="submit"
                disabled={saving || loading || usableAccounts.length === 0}
              >
                {saving
                  ? "Saving transaction…"
                  : <><Plus size={17} /> Record transaction</>}
              </button>
            </div>
          </div>
        </form>
      )}
        </>
      )}
    </section>
  );
}
