import { useCallback, useEffect, useMemo, useState } from "react";
import {
  PiggyBank,
  Plus,
  RefreshCw,
  Target,
  TrendingUp,
  Wallet,
  CalendarDays,
  ArrowDownToLine,
  ArrowUpFromLine,
  X,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";

import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
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

  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(minor || 0) / (10 ** digits));
}

function toMinorUnits(value, currency) {
  const digits = currencyDigits(currency);
  const input = String(value).trim();

  if (!/^\d+(\.\d+)?$/.test(input)) {
    throw new Error("Enter a valid positive amount.");
  }

  const [whole, fraction = ""] = input.split(".");

  if (fraction.length > digits) {
    throw new Error(
      `This currency supports at most ${digits} decimal places.`
    );
  }

  const minor =
    BigInt(whole) * (10n ** BigInt(digits)) +
    BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");

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

export default function Savings() {
  const { activeWorkspace } = useAuth();

  const workspaceId = activeWorkspace?.id;
  const currency =
    activeWorkspace?.functional_currency ||
    activeWorkspace?.currency_code ||
    activeWorkspace?.currency?.code ||
    activeWorkspace?.currency ||
    "KES";

  const [goals, setGoals] = useState([]);
  const { accounts, balances, refreshAccounts } = useAccounts();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [showCreate, setShowCreate] = useState(false);
  const [goalName, setGoalName] = useState("");
  const [description, setDescription] = useState("");
  const [targetAmount, setTargetAmount] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [creating, setCreating] = useState(false);

  const [selectedSavingsAccount, setSelectedSavingsAccount] = useState("");
  const [newSavingsAccountName, setNewSavingsAccountName] = useState("My Savings");
  const [creatingSavingsAccount, setCreatingSavingsAccount] = useState(false);

  const [transfer, setTransfer] = useState(null);
  const [transferAmount, setTransferAmount] = useState("");
  const [transferAccount, setTransferAccount] = useState("");
  const [transferring, setTransferring] = useState(false);

  const loadData = useCallback(async (quiet = false) => {
    if (!workspaceId) {
      setGoals([]);
      setLoading(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const goalData = await financeApi.listSavingsGoals(workspaceId);
      setGoals(goalData.goals || []);
    } catch (err) {
      setError(err.message || "Savings information could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const eligibleAccounts = useMemo(() => {
    const balanceMap = new Map(
      balances.map((item) => [item.ledger_account_id, item])
    );

    return accounts
      .filter((account) => {
        return (
          account.is_active &&
          ["cash", "bank", "mobile_money"].includes(account.account_type) &&
          account.currency === currency &&
          account.ledger_account?.id &&
          balanceMap.has(account.ledger_account.id) &&
          balanceMap.get(account.ledger_account.id).is_active
        );
      })
      .map((account) => ({
        ...account,
        balance_minor:
          balanceMap.get(account.ledger_account.id)?.balance_minor || 0,
      }));
  }, [accounts, balances, currency]);

  // Savings accounts can be shared by multiple goals.
  const savingsAccounts = useMemo(() => {
    return accounts.filter((account) =>
      account.is_active &&
      account.account_type === "savings" &&
      account.currency === currency &&
      account.ledger_account?.id
    );
  }, [accounts, currency]);

  useEffect(() => {
    if (savingsAccounts.length === 0) {
      if (selectedSavingsAccount) setSelectedSavingsAccount("");
      return;
    }

    const selectionExists = savingsAccounts.some(
      (account) => account.id === selectedSavingsAccount
    );

    if (!selectionExists) {
      setSelectedSavingsAccount(savingsAccounts[0].id);
    }
  }, [savingsAccounts, selectedSavingsAccount]);

  const totalSaved = goals.reduce(
    (sum, goal) => sum + Number(goal.saved_amount_minor || 0),
    0
  );

  const totalTarget = goals.reduce(
    (sum, goal) => sum + Number(goal.target_amount_minor || 0),
    0
  );

  const overallProgress = totalTarget > 0
    ? Math.min(100, (totalSaved / totalTarget) * 100)
    : 0;

  async function handleCreateSavingsAccount() {
    const accountName = newSavingsAccountName.trim();

    if (!accountName) {
      setError("Enter a name for your Savings account.");
      return;
    }

    setError("");
    setNotice("");
    setCreatingSavingsAccount(true);

    try {
      const result = await financeApi.createAccount({
        workspace_id: workspaceId,
        name: accountName,
        account_type: "savings",
        currency,
      });

      if (!result?.account?.id) {
        throw new Error("The Savings account was created but its ID was not returned.");
      }

      setSelectedSavingsAccount(result.account.id);
      setNotice("Savings account created. You can now create your goal.");
      await Promise.all([loadData(true), refreshAccounts()]);
    } catch (err) {
      setError(err.message || "Could not create the Savings account.");
    } finally {
      setCreatingSavingsAccount(false);
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    setError("");
    setNotice("");

    if (!selectedSavingsAccount) {
      setError("Select or create a Savings account before creating this goal.");
      return;
    }

    try {
      const amountMinor = toMinorUnits(targetAmount, currency);

      setCreating(true);

      await financeApi.createSavingsGoal({
        workspace_id: workspaceId,
        financial_account_id: selectedSavingsAccount,
        name: goalName.trim(),
        description: description.trim(),
        target_amount_minor: amountMinor,
        currency,
        ...(targetDate ? { target_date: targetDate } : {}),
      });

      setGoalName("");
      setDescription("");
      setTargetAmount("");
      setTargetDate("");
      setShowCreate(false);
      setNotice("Savings goal created successfully.");

      await loadData(true);
    } catch (err) {
      setError(err.message || "Could not create the savings goal.");
    } finally {
      setCreating(false);
    }
  }

  function openTransfer(goal, type) {
    setTransfer({ goal, type });
    setTransferAmount("");
    setTransferAccount(eligibleAccounts[0]?.id || "");
    setError("");
    setNotice("");
  }

  async function handleTransfer(event) {
    event.preventDefault();

    if (!transfer || !transferAccount) {
      setError("Choose an account first.");
      return;
    }

    setError("");
    setNotice("");

    try {
      const amountMinor = toMinorUnits(transferAmount, currency);
      const payload = {
        workspace_id: workspaceId,
        source_account_id: transferAccount,
        amount_minor: amountMinor,
      };

      setTransferring(true);

      if (transfer.type === "contribution") {
        await financeApi.contributeToSavings(transfer.goal.id, payload);
      } else {
        await financeApi.withdrawFromSavings(transfer.goal.id, payload);
      }

      setTransfer(null);
      setTransferAmount("");
      setNotice(
        transfer.type === "contribution"
          ? "Contribution recorded successfully."
          : "Withdrawal recorded successfully."
      );

      await Promise.all([loadData(true), refreshAccounts()]);
    } catch (err) {
      setError(err.message || "The savings transfer could not be completed.");
    } finally {
      setTransferring(false);
    }
  }

  if (!workspaceId) {
    return (
      <div className="savings-page">
        <div className="savings-empty">
          <Wallet size={32} />
          <h2>No workspace available</h2>
          <p>Create or join a workspace to manage savings goals.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="savings-page">
      <div className="savings-header">
        <div>
          <div className="savings-eyebrow">YOUR FINANCIAL PLAN</div>
          <h1>Savings</h1>
          <p>Set goals, build your savings and track your progress.</p>
        </div>

        <div className="savings-header-actions">
          <button
            type="button"
            className="savings-secondary-button"
            onClick={async () => { await Promise.all([loadData(true), refreshAccounts()]); }}
            disabled={refreshing}
          >
            <RefreshCw size={16} className={refreshing ? "savings-spin" : ""} />
            Refresh
          </button>

          <button
            type="button"
            className="savings-primary-button"
            onClick={() => {
              setError("");
              setNotice("");
              setShowCreate(true);
            }}
          >
            <Plus size={17} />
            New goal
          </button>
        </div>
      </div>

      {error && (
        <div className="savings-alert savings-alert-error">
          <AlertCircle size={18} />
          <span>{error}</span>
          <button type="button" onClick={() => setError("")}>
            <X size={16} />
          </button>
        </div>
      )}

      {notice && (
        <div className="savings-alert savings-alert-success">
          <CheckCircle2 size={18} />
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice("")}>
            <X size={16} />
          </button>
        </div>
      )}

      <div className="savings-summary-grid">
        <div className="savings-summary-card">
          <div className="savings-summary-icon savings-icon-purple">
            <PiggyBank size={21} />
          </div>
          <span>Total saved</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : formatMoney(totalSaved, currency)}</strong>
          <small>Across all savings goals</small>
        </div>

        <div className="savings-summary-card">
          <div className="savings-summary-icon savings-icon-blue">
            <Target size={21} />
          </div>
          <span>Total target</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : formatMoney(totalTarget, currency)}</strong>
          <small>{loading ? <Skeleton width="85px" height={10} /> : `${goals.length} savings goals`}</small>
        </div>

        <div className="savings-summary-card">
          <div className="savings-summary-icon savings-icon-green">
            <TrendingUp size={21} />
          </div>
          <span>Overall progress</span>
          <strong>{loading ? <Skeleton width="75px" height={23} /> : `${overallProgress.toFixed(1)}%`}</strong>
          <div className="savings-progress-track">
            <div
              className="savings-progress-fill"
              style={{ width: `${overallProgress}%` }}
            />
          </div>
        </div>
      </div>

      <div className="savings-section-heading">
        <div>
          <h2>Your savings goals</h2>
          <p>Track how close you are to achieving each goal.</p>
        </div>
        <span>{goals.length} {goals.length === 1 ? "goal" : "goals"}</span>
      </div>

      {loading ? (
        <div className="savings-goals-grid savings-skeleton-grid" aria-hidden="true">
          {Array.from({ length: 3 }, (_, index) => (
            <article className="savings-goal-card savings-skeleton-card" key={index}>
              <div className="savings-goal-top">
                <Skeleton width={42} height={42} radius={11} />
                <Skeleton width={72} height={21} radius={20} />
              </div>
              <Skeleton width="65%" height={17} />
              <Skeleton width="82%" height={12} />
              <div className="savings-skeleton-amounts">
                <div>
                  <Skeleton width="65%" height={10} />
                  <Skeleton width="90%" height={17} />
                </div>
                <div>
                  <Skeleton width="55%" height={10} />
                  <Skeleton width="90%" height={17} />
                </div>
              </div>
              <Skeleton width="100%" height={9} radius={20} />
              <div className="savings-skeleton-footer">
                <Skeleton width="35%" height={11} />
                <Skeleton width="40%" height={11} />
              </div>
              <div className="savings-goal-actions">
                <Skeleton height={38} />
                <Skeleton height={38} />
              </div>
            </article>
          ))}
        </div>
      ) : goals.length === 0 ? (
        <div className="savings-empty">
          <div className="savings-empty-icon"><PiggyBank size={30} /></div>
          <h2>Start building your savings</h2>
          <p>Create your first savings goal and give your money a purpose.</p>
          <button
            type="button"
            className="savings-primary-button"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={17} />
            Create your first goal
          </button>
        </div>
      ) : (
        <div className="savings-goals-grid">
          {goals.map((goal) => {
            const progress = Math.min(
              100,
              Math.max(0, Number(goal.progress_percentage || 0))
            );

            return (
              <article className="savings-goal-card" key={goal.id}>
                <div className="savings-goal-top">
                  <div className="savings-goal-icon">
                    <Target size={21} />
                  </div>
                  <span className={
                    goal.is_completed
                      ? "savings-status savings-status-complete"
                      : "savings-status"
                  }>
                    {goal.is_completed ? "Completed" : (goal.status || "Active")}
                  </span>
                </div>

                <h3>{goal.name}</h3>
                {goal.description && (
                  <p className="savings-goal-description">{goal.description}</p>
                )}

                <div className="savings-goal-account">
                  <Wallet size={15} />
                  <span>{goal.financial_account_name || "Savings account"}</span>
                </div>

                <div className="savings-goal-amounts">
                  <div>
                    <span>Saved so far</span>
                    <strong>
                      {formatMoney(goal.saved_amount_minor, currency)}
                    </strong>
                  </div>
                  <div className="savings-target-amount">
                    <span>Target</span>
                    <strong>
                      {formatMoney(goal.target_amount_minor, currency)}
                    </strong>
                  </div>
                </div>

                <div className="savings-progress-track savings-goal-progress">
                  <div
                    className="savings-progress-fill"
                    style={{ width: `${progress}%` }}
                  />
                </div>

                <div className="savings-progress-label">
                  <span>{progress.toFixed(1)}% completed</span>
                  <span>
                    {formatMoney(goal.remaining_amount_minor, currency)} left
                  </span>
                </div>

                {goal.target_date && (
                  <div className="savings-goal-date">
                    <CalendarDays size={15} />
                    Target date: {goal.target_date}
                  </div>
                )}

                <div className="savings-goal-actions">
                  <button
                    type="button"
                    className="savings-primary-button"
                    disabled={goal.is_completed || eligibleAccounts.length === 0}
                    onClick={() => openTransfer(goal, "contribution")}
                  >
                    <ArrowDownToLine size={16} />
                    Contribute
                  </button>
                  <button
                    type="button"
                    className="savings-secondary-button"
                    disabled={
                      Number(goal.saved_amount_minor || 0) <= 0 ||
                      eligibleAccounts.length === 0
                    }
                    onClick={() => openTransfer(goal, "withdrawal")}
                  >
                    <ArrowUpFromLine size={16} />
                    Withdraw
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {eligibleAccounts.length === 0 && !loading && (
        <div className="savings-account-notice">
          <Wallet size={18} />
          <span>
            Add an active cash, bank or mobile-money account in {currency}
            before making savings transfers.
          </span>
        </div>
      )}

      {showCreate && (
        <div className="savings-modal-backdrop">
          <div className="savings-modal">
            <div className="savings-modal-heading">
              <div>
                <h2>Create savings goal</h2>
                <p>Give your savings a target to work towards.</p>
              </div>
              <button
                type="button"
                className="savings-close-button"
                onClick={() => setShowCreate(false)}
                disabled={creating}
              >
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleCreate}>
              {savingsAccounts.length > 0 ? (
                <label>
                  Savings account
                  <select
                    value={selectedSavingsAccount}
                    onChange={(event) =>
                      setSelectedSavingsAccount(event.target.value)
                    }
                    required
                  >
                    {savingsAccounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name} ({account.currency})
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <div className="savings-inline-account-create">
                  <div className="savings-inline-account-heading">
                    <Wallet size={19} />
                    <strong>Create a Savings account first</strong>
                  </div>
                  <p>
                    Your savings goals need a Savings account. You can use
                    the same account for more than one goal.
                  </p>
                  <label>
                    Savings account name
                    <input
                      value={newSavingsAccountName}
                      onChange={(event) =>
                        setNewSavingsAccountName(event.target.value)
                      }
                      placeholder="e.g. My Savings"
                      maxLength={150}
                    />
                  </label>
                  <button
                    type="button"
                    className="savings-primary-button"
                    onClick={handleCreateSavingsAccount}
                    disabled={creatingSavingsAccount || !newSavingsAccountName.trim()}
                  >
                    <Plus size={16} />
                    {creatingSavingsAccount
                      ? "Creating account..."
                      : "Create Savings account"}
                  </button>
                </div>
              )}

              <label>
                Goal name
                <input
                  value={goalName}
                  onChange={(event) => setGoalName(event.target.value)}
                  placeholder="e.g. Emergency fund"
                  maxLength={120}
                  required
                />
              </label>

              <label>
                Description (optional)
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What are you saving for?"
                  rows={3}
                />
              </label>

              <label>
                Target amount ({currency})
                <input
                  type="number"
                  min="0.01"
                  step={currencyDigits(currency) === 0 ? "1" : "0.01"}
                  value={targetAmount}
                  onChange={(event) => setTargetAmount(event.target.value)}
                  placeholder="Enter target amount"
                  required
                />
              </label>

              <label>
                Target date (optional)
                <input
                  type="date"
                  min={localDate(new Date())}
                  value={targetDate}
                  onChange={(event) => setTargetDate(event.target.value)}
                />
              </label>

              <div className="savings-modal-actions">
                <button
                  type="button"
                  className="savings-secondary-button"
                  onClick={() => setShowCreate(false)}
                  disabled={creating}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="savings-primary-button"
                  disabled={creating || creatingSavingsAccount || !selectedSavingsAccount}
                >
                  {creating ? "Creating..." : "Create goal"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {transfer && (
        <div className="savings-modal-backdrop">
          <div className="savings-modal">
            <div className="savings-modal-heading">
              <div>
                <h2>
                  {transfer.type === "contribution"
                    ? "Add to savings"
                    : "Withdraw from savings"}
                </h2>
                <p>{transfer.goal.name}</p>
              </div>
              <button
                type="button"
                className="savings-close-button"
                onClick={() => setTransfer(null)}
                disabled={transferring}
              >
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleTransfer}>
              <label>
                {transfer.type === "contribution"
                  ? "Take money from"
                  : "Send money to"}
                <select
                  value={transferAccount}
                  onChange={(event) => setTransferAccount(event.target.value)}
                  required
                >
                  {eligibleAccounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name} — {formatMoney(account.balance_minor, currency)}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Amount ({currency})
                <input
                  type="number"
                  min="0.01"
                  step={currencyDigits(currency) === 0 ? "1" : "0.01"}
                  value={transferAmount}
                  onChange={(event) => setTransferAmount(event.target.value)}
                  placeholder="Enter amount"
                  required
                />
              </label>

              <div className="savings-transfer-note">
                {transfer.type === "contribution"
                  ? "The amount will be transferred from your selected account into this savings goal."
                  : "The amount will be transferred from this savings goal into your selected account."}
              </div>

              <div className="savings-modal-actions">
                <button
                  type="button"
                  className="savings-secondary-button"
                  onClick={() => setTransfer(null)}
                  disabled={transferring}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="savings-primary-button"
                  disabled={transferring || !transferAccount}
                >
                  {transferring
                    ? "Processing..."
                    : transfer.type === "contribution"
                      ? "Confirm contribution"
                      : "Confirm withdrawal"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
