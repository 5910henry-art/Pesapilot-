import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  HandCoins,
  Plus,
  RefreshCw,
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Clock3,
  AlertCircle,
  X,
} from "lucide-react";

import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { loansApi } from "../api/client";
import { Skeleton } from "../components/Skeleton";

const MINOR_UNITS = {
  BIF: 0, CLP: 0, DJF: 0, GNF: 0, JPY: 0, KMF: 0,
  KRW: 0, RWF: 0, UGX: 0, VND: 0, VUV: 0, XAF: 0,
  XOF: 0, XPF: 0,
};

function digitsFor(currency) {
  return MINOR_UNITS[currency] ?? 2;
}

function toMinorUnits(value, currency) {
  const digits = digitsFor(currency);
  const input = String(value).trim();

  if (!/^\d+(\.\d+)?$/.test(input)) {
    throw new Error("Enter a valid loan amount.");
  }

  const [whole, fraction = ""] = input.split(".");

  if (fraction.length > digits) {
    throw new Error(`Use no more than ${digits} decimal places.`);
  }

  const amount =
    BigInt(whole) * (10n ** BigInt(digits)) +
    BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");

  if (amount <= 0n || amount > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("Enter a positive amount within the supported range.");
  }

  return Number(amount);
}

function money(amount, currency) {
  const digits = digitsFor(currency);

  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(amount || 0) / (10 ** digits));
}

function todayLocal() {
  const date = new Date();
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultDueDate() {
  const date = new Date();
  date.setMonth(date.getMonth() + 1);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

const INITIAL_FORM = {
  direction: "borrowed",
  counterparty_name: "",
  counterparty_phone: "",
  loan_type: "personal",
  amount: "",
  interest_method: "none",
  interest_rate_percent: "0",
  repayment_frequency: "monthly",
  term_count: "1",
  start_date: todayLocal(),
  due_date: defaultDueDate(),
  description: "",
};

const LOAN_TYPE_LABELS = {
  soft: "Soft loan",
  interest_free: "Interest-free",
  interest_bearing: "Interest-bearing",
  emergency: "Emergency",
  business: "Business",
  personal: "Personal",
};

const STATUS_LABELS = {
  draft: "Draft",
  active: "Active",
  paid: "Paid",
  overdue: "Overdue",
  disputed: "Disputed",
  restructured: "Restructured",
  written_off: "Written off",
  cancelled: "Cancelled",
};

export default function Loans() {
  const { activeWorkspace } = useAuth();

  const workspaceId = activeWorkspace?.id;
  const currency =
    activeWorkspace?.functional_currency ||
    activeWorkspace?.currency_code ||
    activeWorkspace?.currency?.code ||
    activeWorkspace?.currency ||
    "KES";

  const [loans, setLoans] = useState([]);
  const workspaceRequestRef = useRef(0);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [showForm, setShowForm] = useState(false);
  const [editingLoan, setEditingLoan] = useState(null);
  const [form, setForm] = useState(INITIAL_FORM);
  const [saving, setSaving] = useState(false);
  const [expandedLoan, setExpandedLoan] = useState(null);
  const [loanDetails, setLoanDetails] = useState({});
  const [loadingDetails, setLoadingDetails] = useState(null);
  const [activating, setActivating] = useState(null);
  const [disbursing, setDisbursing] = useState(null);
  const [disbursementLoan, setDisbursementLoan] = useState(null);
  const [disbursementForm, setDisbursementForm] = useState({
    financial_account_id: "",
    disbursement_date: todayLocal(),
    payment_method: "cash",
    notes: "",
  });
  const { accounts: paymentAccounts } = useAccounts();
  const [repaymentLoan, setRepaymentLoan] = useState(null);
  const [repaymentForm, setRepaymentForm] = useState({
    amount: "",
    payment_date: todayLocal(),
    payment_method: "cash",
    payment_source: "registered",
    reference: "",
    notes: "",
  });
  const [savingPayment, setSavingPayment] = useState(false);
  const [confirmingPayment, setConfirmingPayment] = useState(null);
  const [paymentAccountSelections, setPaymentAccountSelections] = useState({});
  const [editingPaymentId, setEditingPaymentId] = useState(null);
  const [paymentEditForm, setPaymentEditForm] = useState({
    amount: "",
    payment_date: todayLocal(),
    payment_method: "cash",
    payment_source: "registered",
    reference: "",
    notes: "",
  });
  const [updatingPayment, setUpdatingPayment] = useState(null);
  const [deletingPayment, setDeletingPayment] = useState(null);


  const loadLoans = useCallback(async (quiet = false) => {
    const requestId = ++workspaceRequestRef.current;

    if (!workspaceId) {
      setLoans([]);
      setLoading(false);
      setRefreshing(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const data = await loansApi.listLoans(workspaceId);

      if (requestId === workspaceRequestRef.current) {
        setLoans(data.loans || []);
      }
    } catch (err) {
      if (requestId === workspaceRequestRef.current) {
        setError(err.message || "Loans could not be loaded.");
      }
    } finally {
      if (requestId === workspaceRequestRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [workspaceId]);

  useEffect(() => {
    setLoans([]);
    setLoanDetails({});
    setExpandedLoan(null);
    setLoadingDetails(null);
    setError("");
    setNotice("");

    loadLoans();
  }, [loadLoans]);



  const visibleLoans = useMemo(() => {
    if (filter === "all") return loans;
    if (filter === "borrowed" || filter === "lent") {
      return loans.filter((loan) => loan.direction === filter);
    }
    return loans.filter((loan) => loan.status === filter);
  }, [loans, filter]);

  const totals = useMemo(() => {
    const borrowed = loans
      .filter((loan) => loan.direction === "borrowed")
      .reduce((sum, loan) => sum + Number(loan.principal_minor || 0), 0);

    const lent = loans
      .filter((loan) => loan.direction === "lent")
      .reduce((sum, loan) => sum + Number(loan.principal_minor || 0), 0);

    const active = loans.filter((loan) =>
      ["active", "overdue", "restructured"].includes(loan.status)
    ).length;

    return { borrowed, lent, active };
  }, [loans]);

  function updateForm(field, value) {
    setForm((current) => {
      const next = { ...current, [field]: value };

      if (field === "loan_type" && value === "interest_free") {
        next.interest_method = "none";
        next.interest_rate_percent = "0";
      }

      if (field === "interest_method" && value === "none") {
        next.interest_rate_percent = "0";
      }

      if (field === "repayment_frequency" && value === "one_time") {
        next.term_count = "1";
      }

      return next;
    });
  }

  function openEditLoan(loan) {
    const digits = digitsFor(loan.currency_code);

    setEditingLoan(loan);
    setForm({
      direction: loan.direction,
      counterparty_name: loan.counterparty_name || "",
      counterparty_phone: loan.counterparty_phone || "",
      loan_type: loan.loan_type,
      amount: (Number(loan.principal_minor || 0) / (10 ** digits)).toFixed(digits),
      interest_method: loan.interest_method || "none",
      interest_rate_percent: String(loan.interest_rate_percent ?? "0"),
      repayment_frequency: loan.repayment_frequency,
      term_count: String(loan.term_count || 1),
      start_date: loan.start_date,
      due_date: loan.due_date,
      description: loan.description || "",
    });
    setError("");
    setNotice("");
    setShowForm(true);
  }

  async function handleCreate(event) {
    event.preventDefault();
    setError("");
    setNotice("");

    if (!workspaceId) {
      setError("Select a workspace before recording a loan.");
      return;
    }

    try {
      const principalMinor = toMinorUnits(form.amount, currency);
      const termCount = Number(form.term_count);
      const rate = Number(form.interest_rate_percent);

      if (!Number.isInteger(termCount) || termCount < 1 || termCount > 1200) {
        throw new Error("Term count must be between 1 and 1200.");
      }

      if (!Number.isFinite(rate) || rate < 0 || rate > 1000) {
        throw new Error("Enter a valid annual interest rate.");
      }

      if (form.interest_method === "none" && rate !== 0) {
        throw new Error("An interest-free loan must have a zero interest rate.");
      }

      if (form.loan_type === "interest_free" && form.interest_method !== "none") {
        throw new Error("Interest-free loans cannot have an interest method.");
      }

      if (form.due_date < form.start_date) {
        throw new Error("The final due date cannot be before the start date.");
      }

      setSaving(true);

      const payload = {
        workspace_id: workspaceId,
        direction: form.direction,
        counterparty_name: form.counterparty_name.trim(),
        counterparty_phone: form.counterparty_phone.trim() || null,
        loan_type: form.loan_type,
        principal_minor: principalMinor,
        currency_code: currency,
        interest_method: form.interest_method,
        interest_rate_percent: String(rate),
        repayment_frequency: form.repayment_frequency,
        term_count: termCount,
        start_date: form.start_date,
        due_date: form.due_date,
        description: form.description.trim() || null,
      };

      if (editingLoan) {
        await loansApi.updateLoan(editingLoan.id, payload);
      } else {
        await loansApi.createLoan(payload);
      }

      setForm({ ...INITIAL_FORM, start_date: todayLocal(), due_date: defaultDueDate() });
      setEditingLoan(null);
      setShowForm(false);
      setNotice(
        editingLoan
          ? "Draft loan updated successfully."
          : "Loan recorded as a draft. Activate it when the repayment plan is ready."
      );
      await loadLoans(true);
    } catch (err) {
      setError(err.message || (editingLoan
        ? "The draft loan could not be updated."
        : "The loan could not be recorded."));
    } finally {
      setSaving(false);
    }
  }

  async function toggleDetails(loanId) {
    if (expandedLoan === loanId) {
      setExpandedLoan(null);
      return;
    }

    setExpandedLoan(loanId);

    if (loanDetails[loanId]) return;

    setLoadingDetails(loanId);
    setError("");

    try {
      const data = await loansApi.getLoan(loanId, workspaceId);
      setLoanDetails((current) => ({
        ...current,
        [loanId]: data,
      }));
    } catch (err) {
      setError(err.message || "Loan details could not be loaded.");
    } finally {
      setLoadingDetails(null);
    }
  }

  async function handleActivate(loanId) {
    if (!window.confirm("Activate this loan and generate its repayment schedule?")) {
      return;
    }

    setActivating(loanId);
    setError("");
    setNotice("");

    try {
      const data = await loansApi.activateLoan(loanId, workspaceId);

      setLoanDetails((current) => ({
        ...current,
        [loanId]: {
          ...(current[loanId] || {}),
          ...data,
        },
      }));

      setNotice("Loan activated and repayment schedule generated.");
      await loadLoans(true);
    } catch (err) {
      setError(err.message || "The loan could not be activated.");
    } finally {
      setActivating(null);
    }
  }

  function openDisbursementForm(loan) {
    const cashAccounts = paymentAccounts.filter((account) => (
      account.is_active &&
      account.currency === loan.currency_code &&
      account.account_type === "cash"
    ));

    setDisbursementLoan(loan.id);
    setDisbursementForm({
      financial_account_id: cashAccounts[0]?.id || "",
      disbursement_date: todayLocal(),
      payment_method: "cash",
      notes: "",
    });
    setError("");
    setNotice("");
  }

  async function handleDisburseLoan(loan) {
    if (!disbursementForm.financial_account_id) {
      setError("Choose the account used for this disbursement.");
      return;
    }

    setDisbursing(loan.id);
    setError("");
    setNotice("");

    try {
      await loansApi.disburseLoan(loan.id, {
        workspace_id: workspaceId,
        financial_account_id: disbursementForm.financial_account_id,
        disbursement_date: disbursementForm.disbursement_date,
        payment_method: disbursementForm.payment_method,
        notes: disbursementForm.notes.trim() || null,
      });

      setDisbursementLoan(null);
      setNotice("Loan disbursement posted successfully.");

      const refreshed = await loansApi.getLoan(loan.id, workspaceId);
      setLoanDetails((current) => ({
        ...current,
        [loan.id]: refreshed,
      }));
      await loadLoans(true);
    } catch (err) {
      setError(err.message || "The loan could not be disbursed.");
    } finally {
      setDisbursing(null);
    }
  }

  function openRepaymentForm(loan) {
    setRepaymentLoan(loan.id);
    setRepaymentForm({
      amount: "",
      payment_date: todayLocal(),
      payment_method: "cash",
      payment_source: "registered",
      reference: "",
      notes: "",
    });
    setError("");
    setNotice("");
  }

  async function handleRecordLoanPayment(loan) {
    setSavingPayment(true);
    setError("");
    setNotice("");

    try {
      const amountMinor = toMinorUnits(
        repaymentForm.amount,
        loan.currency_code
      );

      await loansApi.recordPayment(loan.id, {
        workspace_id: workspaceId,
        amount_minor: amountMinor,
        payment_date: repaymentForm.payment_date,
        payment_method: repaymentForm.payment_method,
        payment_source: repaymentForm.payment_source,
        reference: repaymentForm.reference.trim(),
        notes: repaymentForm.notes.trim() || null,
      });

      setRepaymentLoan(null);
      setRepaymentForm({
        amount: "",
        payment_date: todayLocal(),
        payment_method: "cash",
        payment_source: "registered",
        reference: "",
        notes: "",
      });
      setNotice("Repayment recorded as pending. Select an account to confirm it.");

      try {
        const refreshed = await loansApi.getLoan(loan.id, workspaceId);
        setLoanDetails((current) => ({ ...current, [loan.id]: refreshed }));
        await loadLoans(true);
      } catch (refreshError) {
        setError("Repayment was recorded, but the loan details could not refresh. Please refresh the loan.");
      }
    } catch (err) {
      setError(err.message || "The repayment could not be recorded.");
    } finally {
      setSavingPayment(false);
    }
  }


  function openEditPayment(payment, currency) {
    const digits = digitsFor(currency);
    const amount = Number(payment.amount_minor || 0) / (10 ** digits);

    setEditingPaymentId(payment.id);
    setPaymentEditForm({
      amount: amount.toFixed(digits),
      payment_date: payment.payment_date,
      payment_method: payment.payment_method,
      payment_source: payment.payment_source || "registered",
      reference: payment.reference || "",
      notes: payment.notes || "",
    });
    setError("");
    setNotice("");
  }

  async function handleUpdateLoanPayment(loan, payment) {
    setUpdatingPayment(payment.id);
    setError("");
    setNotice("");

    try {
      const amountMinor = toMinorUnits(
        paymentEditForm.amount,
        loan.currency_code
      );

      await loansApi.updatePayment(loan.id, payment.id, {
        workspace_id: workspaceId,
        amount_minor: amountMinor,
        payment_date: paymentEditForm.payment_date,
        payment_method: paymentEditForm.payment_method,
        payment_source: paymentEditForm.payment_source,
        reference: paymentEditForm.reference.trim(),
        notes: paymentEditForm.notes.trim() || null,
      });

      setEditingPaymentId(null);
      setNotice("Pending repayment updated successfully.");

      const refreshed = await loansApi.getLoan(loan.id, workspaceId);
      setLoanDetails((current) => ({
        ...current,
        [loan.id]: refreshed,
      }));
      await loadLoans(true);
    } catch (err) {
      setError(err.message || "The pending repayment could not be updated.");
    } finally {
      setUpdatingPayment(null);
    }
  }

  async function handleDeleteLoanPayment(loan, payment) {
    const confirmed = window.confirm(
      `Delete this pending repayment of ${money(payment.amount_minor, loan.currency_code)}? This cannot be undone.`
    );

    if (!confirmed) return;

    setDeletingPayment(payment.id);
    setError("");
    setNotice("");

    try {
      await loansApi.deletePayment(loan.id, payment.id, {
        workspace_id: workspaceId,
      });

      if (editingPaymentId === payment.id) {
        setEditingPaymentId(null);
      }

      setNotice("Pending repayment deleted successfully.");

      const refreshed = await loansApi.getLoan(loan.id, workspaceId);
      setLoanDetails((current) => ({
        ...current,
        [loan.id]: refreshed,
      }));
      await loadLoans(true);
    } catch (err) {
      setError(err.message || "The pending repayment could not be deleted.");
    } finally {
      setDeletingPayment(null);
    }
  }

  async function handleConfirmLoanPayment(loan, payment) {
    const isExternal = payment.payment_source === "external";
    let accountId = "";

    if (!isExternal) {
      const eligibleAccounts = paymentAccounts.filter((account) => (
        account.is_active &&
        account.currency === loan.currency_code &&
        ["cash", "bank", "mobile_money"].includes(account.account_type)
      ));

      const savedAccountId = paymentAccountSelections[payment.id];
      accountId = eligibleAccounts.some(
        (account) => account.id === savedAccountId
      )
        ? savedAccountId
        : eligibleAccounts[0]?.id || "";

      if (!accountId) {
        setError("No active matching account is available for this repayment.");
        return;
      }
    }

    setConfirmingPayment(payment.id);
    setError("");
    setNotice("");

    try {
      await loansApi.confirmPayment(loan.id, payment.id, {
        workspace_id: workspaceId,
        ...(isExternal ? {} : { financial_account_id: accountId }),
      });

      setNotice("Repayment confirmed and accounting updated.");

      try {
        const refreshed = await loansApi.getLoan(loan.id, workspaceId);
        setLoanDetails((current) => ({ ...current, [loan.id]: refreshed }));
        await loadLoans(true);
      } catch (refreshError) {
        setError("Repayment was confirmed, but the loan details could not refresh. Please refresh the loan.");
      }
    } catch (err) {
      setError(err.message || "The repayment could not be confirmed.");
    } finally {
      setConfirmingPayment(null);
    }
  }

  return (
    <div className="page-content loans-page">
      <div className="loans-header">
        <div>
          <span className="loans-eyebrow">PERSONAL FINANCE</span>
          <h1>Loans</h1>
          <p>Keep track of money you borrow and money you lend.</p>
        </div>

        <div className="loans-header-actions">
          <button
            type="button"
            className="loans-secondary-button"
            onClick={() => loadLoans(true)}
            disabled={refreshing || loading}
          >
            <RefreshCw size={16} className={refreshing ? "loans-spin" : ""} />
            Refresh
          </button>
          <button
            type="button"
            className="loans-primary-button"
            onClick={() => {
              setError("");
              setNotice("");
              setShowForm(true);
            }}
          >
            <Plus size={17} />
            Record loan
          </button>
        </div>
      </div>

      {error && (
        <div className="loans-alert loans-alert-error">
          <AlertCircle size={17} />
          {error}
        </div>
      )}

      {notice && (
        <div className="loans-alert loans-alert-success">
          <CheckCircle2 size={17} />
          {notice}
        </div>
      )}

      <div className="loans-summary-grid">
        <div className="loans-summary-card">
          <div className="loans-summary-icon loans-purple">
            <ArrowDownLeft size={20} />
          </div>
          <span>Total borrowed</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : money(totals.borrowed, currency)}</strong>
          <small>Recorded borrowing principal</small>
        </div>

        <div className="loans-summary-card">
          <div className="loans-summary-icon loans-blue">
            <ArrowUpRight size={20} />
          </div>
          <span>Total lent</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : money(totals.lent, currency)}</strong>
          <small>Recorded lending principal</small>
        </div>

        <div className="loans-summary-card">
          <div className="loans-summary-icon loans-green">
            <HandCoins size={20} />
          </div>
          <span>Active loans</span>
          <strong>{loading ? <Skeleton width="55px" height={23} /> : totals.active}</strong>
          <small>Active, overdue or restructured</small>
        </div>
      </div>

      <div className="loans-workspace-tabs">
        <button type="button" className="active">
          My Loans
        </button>
        <button
          type="button"
          onClick={() => setNotice("Lending Business tools will be added after the loan application and borrower-management backend is ready.")}
        >
          Lending Business <span>Coming next</span>
        </button>
      </div>

      <section className="loans-list-section">
        <div className="loans-section-heading">
          <div>
            <h2>My loan records</h2>
            <p>Manage your personal borrowing and lending.</p>
          </div>

          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter loans"
          >
            <option value="all">All loans</option>
            <option value="borrowed">Borrowed</option>
            <option value="lent">Lent</option>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="overdue">Overdue</option>
            <option value="paid">Paid</option>
          </select>
        </div>

        {loading ? (
          <div className="loans-record-list loans-skeleton-list" aria-hidden="true">
            {Array.from({ length: 4 }, (_, index) => (
              <article className="loans-record loans-skeleton-record" key={index}>
                <div className="loans-record-main">
                  <Skeleton width={46} height={46} radius={13} />
                  <div className="loans-record-info">
                    <div className="loans-skeleton-title">
                      <Skeleton width="48%" height={16} />
                      <Skeleton width={68} height={20} radius={20} />
                    </div>
                    <Skeleton width="65%" height={11} />
                    <Skeleton width="75%" height={11} />
                  </div>
                  <Skeleton width={25} height={25} radius={8} />
                </div>
                <div className="loans-skeleton-bottom">
                  <Skeleton width="28%" height={12} />
                  <Skeleton width="35%" height={15} />
                </div>
              </article>
            ))}
          </div>
        ) : visibleLoans.length === 0 ? (
          <div className="loans-empty">
            <div className="loans-empty-icon">
              <HandCoins size={26} />
            </div>
            <h3>No loans to show</h3>
            <p>
              {filter === "all"
                ? "Record money you have borrowed or lent to start tracking it here."
                : "There are no loans matching this filter."}
            </p>
            {filter === "all" && (
              <button
                type="button"
                className="loans-primary-button"
                onClick={() => setShowForm(true)}
              >
                <Plus size={16} />
                Record your first loan
              </button>
            )}
          </div>
        ) : (
          <div className="loans-record-list">
            {visibleLoans.map((loan) => {
              const expanded = expandedLoan === loan.id;
              const details = loanDetails[loan.id];

              return (
                <article className="loans-record" key={loan.id}>
                  <div className="loans-record-main">
                    <div className={`loans-record-icon ${loan.direction}`}>
                      {loan.direction === "borrowed"
                        ? <ArrowDownLeft size={21} />
                        : <ArrowUpRight size={21} />}
                    </div>

                    <div className="loans-record-info">
                      <div className="loans-record-title">
                        <h3>{loan.counterparty_name}</h3>
                        <span className={`loans-status status-${loan.status}`}>
                          {STATUS_LABELS[loan.status] || loan.status}
                        </span>
                      </div>
                      <p>
                        {loan.direction === "borrowed" ? "Borrowed from" : "Lent to"}{" "}
                        {loan.counterparty_name}
                        {" · "}
                        {LOAN_TYPE_LABELS[loan.loan_type] || loan.loan_type}
                      </p>
                      <div className="loans-record-meta">
                        <span><CalendarDays size={14} /> Due {loan.due_date}</span>
                        <span>{loan.repayment_frequency.replace("_", " ")}</span>
                        {loan.interest_method !== "none" && (
                          <span>{loan.interest_rate_percent}% annual interest</span>
                        )}
                      </div>
                    </div>

                    <div className="loans-record-amount">
                      <strong>{money(loan.principal_minor, loan.currency_code)}</strong>
                      <small>Principal</small>
                    </div>

                    <button
                      type="button"
                      className="loans-expand-button"
                      onClick={() => toggleDetails(loan.id)}
                      aria-label={expanded ? "Hide loan details" : "Show loan details"}
                    >
                      {expanded ? <ChevronUp size={19} /> : <ChevronDown size={19} />}
                    </button>
                  </div>

                  {expanded && createPortal((

                    <div
                      className="loans-drawer-backdrop"
                      onClick={() => setExpandedLoan(null)}
                    >
                      <aside
                        className="loans-detail-drawer"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby={`loan-drawer-title-${loan.id}`}
                        onClick={(event) => event.stopPropagation()}
                      >
                        <div className="loans-drawer-header">
                          <div>
                            <span className="loans-eyebrow">LOAN DETAILS</span>
                            <h2 id={`loan-drawer-title-${loan.id}`}>
                              {loan.counterparty_name}
                            </h2>
                            <p>
                              {loan.direction === "lent" ? "Money lent" : "Money borrowed"}
                              {" · "}
                              {money(loan.principal_minor, loan.currency_code)}
                            </p>
                          </div>
                          <button
                            type="button"
                            className="loans-drawer-close"
                            onClick={() => setExpandedLoan(null)}
                            aria-label="Close loan details"
                          >
                            <X size={21} />
                          </button>
                        </div>
                        <div className="loans-detail-panel">
                      {loadingDetails === loan.id ? (
                        <div className="loans-detail-grid loans-detail-skeleton" aria-hidden="true">
                          {Array.from({ length: 4 }, (_, index) => (
                            <div key={index}>
                              <Skeleton width="65%" height={11} />
                              <Skeleton width="85%" height={16} />
                            </div>
                          ))}
                        </div>
                      ) : details ? (
                        <>
                          <div className="loans-detail-grid">
                            <div>
                              <span>Start date</span>
                              <strong>{loan.start_date}</strong>
                            </div>
                            <div>
                              <span>Final due date</span>
                              <strong>{loan.due_date}</strong>
                            </div>
                            <div>
                              <span>Repayment terms</span>
                              <strong>{loan.term_count} instalment(s)</strong>
                            </div>
                            <div>
                              <span>Interest method</span>
                              <strong>{loan.interest_method}</strong>
                            </div>
                          </div>

                          {loan.description && (
                            <p className="loans-description">{loan.description}</p>
                          )}

                          <section className="loans-disbursement-section">
                            <h4>Loan disbursement</h4>

                            {details.disbursement ? (
                              <div className="loans-disbursement-record">
                                <div>
                                  <span>Amount</span>
                                  <strong>{money(
                                    details.disbursement.amount_minor,
                                    loan.currency_code
                                  )}</strong>
                                </div>
                                <div>
                                  <span>Date</span>
                                  <strong>{details.disbursement.disbursement_date}</strong>
                                </div>
                                <div>
                                  <span>Payment method</span>
                                  <strong>{details.disbursement.payment_method.replace("_", " ")}</strong>
                                </div>
                                {details.disbursement.notes && (
                                  <p className="loans-muted">{details.disbursement.notes}</p>
                                )}
                                <small className="loans-disbursement-confirmed">
                                  <CheckCircle2 size={14} /> Disbursement posted
                                </small>
                              </div>
                            ) : loan.status === "active" ? (
                              disbursementLoan !== loan.id ? (
                                <div>
                                  <p className="loans-muted">
                                    This loan is active but its principal has not yet been disbursed.
                                  </p>
                                  <button
                                    type="button"
                                    className="loans-primary-button"
                                    onClick={() => openDisbursementForm(loan)}
                                  >
                                    <ArrowUpRight size={16} />
                                    Record disbursement
                                  </button>
                                </div>
                              ) : (
                                <form
                                  className="loans-repayment-form loans-disbursement-form"
                                  onSubmit={(event) => {
                                    event.preventDefault();
                                    handleDisburseLoan(loan);
                                  }}
                                >
                                  <h4>
                                    {loan.direction === "lent"
                                      ? "Disburse money to borrower"
                                      : "Record money received"}
                                  </h4>

                                  <p className="loans-muted">
                                    Principal: {money(loan.principal_minor, loan.currency_code)}
                                  </p>

                                  <label>
                                    {loan.direction === "lent"
                                      ? "Pay from account"
                                      : "Receive into account"}
                                    <select
                                      required
                                      value={disbursementForm.financial_account_id}
                                      onChange={(event) => {
                                        const selected = paymentAccounts.find(
                                          (account) => account.id === event.target.value
                                        );
                                        setDisbursementForm((current) => ({
                                          ...current,
                                          financial_account_id: event.target.value,
                                          payment_method: selected?.account_type || current.payment_method,
                                        }));
                                      }}
                                    >
                                      <option value="">Choose an account</option>
                                      {paymentAccounts
                                        .filter((account) => (
                                          account.is_active &&
                                          account.currency === loan.currency_code &&
                                          ["cash", "bank", "mobile_money"].includes(account.account_type) &&
                                          (
                                            disbursementForm.payment_method === "other" ||
                                            account.account_type === disbursementForm.payment_method
                                          )
                                        ))
                                        .map((account) => (
                                          <option key={account.id} value={account.id}>
                                            {account.name} ({
                                              account.account_type === "cash"
                                                ? "Cash wallet"
                                                : account.account_type.replace("_", " ")
                                            })
                                          </option>
                                        ))}
                                    </select>
                                  </label>

                                  <label>
                                    Disbursement date
                                    <input
                                      type="date"
                                      required
                                      value={disbursementForm.disbursement_date}
                                      onChange={(event) => setDisbursementForm((current) => ({
                                        ...current,
                                        disbursement_date: event.target.value,
                                      }))}
                                    />
                                  </label>

                                  <label>
                                    Payment method
                                    <select
                                      value={disbursementForm.payment_method}
                                      onChange={(event) => {
                                        const method = event.target.value;
                                        const eligibleAccounts = paymentAccounts.filter((account) => (
                                          account.is_active &&
                                          account.currency === loan.currency_code &&
                                          ["cash", "bank", "mobile_money"].includes(account.account_type)
                                        ));
                                        const matchingAccounts = method === "other"
                                          ? eligibleAccounts
                                          : eligibleAccounts.filter(
                                              (account) => account.account_type === method
                                            );

                                        setDisbursementForm((current) => ({
                                          ...current,
                                          payment_method: method,
                                          financial_account_id: matchingAccounts.some(
                                            (account) => account.id === current.financial_account_id
                                          )
                                            ? current.financial_account_id
                                            : matchingAccounts[0]?.id || "",
                                        }));
                                      }}
                                    >
                                      <option value="cash">Cash</option>
                                      <option value="bank">Bank</option>
                                      <option value="mobile_money">Mobile money</option>
                                      <option value="other">Other</option>
                                    </select>
                                  </label>

                                  <label>
                                    Notes (optional)
                                    <textarea
                                      rows={2}
                                      maxLength={2000}
                                      value={disbursementForm.notes}
                                      onChange={(event) => setDisbursementForm((current) => ({
                                        ...current,
                                        notes: event.target.value,
                                      }))}
                                      placeholder="Add a disbursement note..."
                                    />
                                  </label>

                                  {!paymentAccounts.some((account) => (
                                    account.is_active &&
                                    account.currency === loan.currency_code &&
                                    ["cash", "bank", "mobile_money"].includes(account.account_type) &&
                                    (
                                      disbursementForm.payment_method === "other" ||
                                      account.account_type === disbursementForm.payment_method
                                    )
                                  )) && (
                                    <small className="loans-muted">
                                      {disbursementForm.payment_method === "cash"
                                        ? `No active cash wallet in ${loan.currency_code}. Add a cash account before disbursing.`
                                        : disbursementForm.payment_method === "bank"
                                          ? `No active bank account in ${loan.currency_code}. Add a bank account before disbursing.`
                                          : disbursementForm.payment_method === "mobile_money"
                                            ? `No active mobile money account in ${loan.currency_code}. Add one before disbursing.`
                                            : `No eligible account in ${loan.currency_code} is available. Add an account before disbursing.`}
                                    </small>
                                  )}

                                  <div className="loans-modal-actions">
                                    <button
                                      type="button"
                                      className="loans-secondary-button"
                                      onClick={() => setDisbursementLoan(null)}
                                      disabled={disbursing === loan.id}
                                    >
                                      Cancel
                                    </button>
                                    <button
                                      type="submit"
                                      className="loans-primary-button"
                                      disabled={
                                        disbursing === loan.id ||
                                        !disbursementForm.financial_account_id ||
                                        !paymentAccounts.some((account) => (
                                          account.id === disbursementForm.financial_account_id &&
                                          account.is_active &&
                                          account.currency === loan.currency_code &&
                                          ["cash", "bank", "mobile_money"].includes(account.account_type) &&
                                          (
                                            disbursementForm.payment_method === "other" ||
                                            account.account_type === disbursementForm.payment_method
                                          )
                                        ))
                                      }
                                    >
                                      {disbursing === loan.id
                                        ? "Posting..."
                                        : "Confirm disbursement"}
                                    </button>
                                  </div>
                                </form>
                              )
                            ) : (
                              <p className="loans-muted">
                                Disbursement becomes available after the loan is activated.
                              </p>
                            )}
                          </section>

                          {details.installments?.length > 0 && (() => {
                            const scheduledMinor = details.installments.reduce(
                              (sum, item) => sum + Number(item.amount_minor || 0),
                              0
                            );
                            const outstandingMinor = details.installments.reduce(
                              (sum, item) => sum + Number(
                                item.outstanding_minor ??
                                Math.max(0, Number(item.amount_minor || 0) - Number(item.paid_minor || 0))
                              ),
                              0
                            );
                            const paidMinor = Math.max(0, scheduledMinor - outstandingMinor);

                            return (
                              <div className="loans-balance-summary">
                                <div>
                                  <span>Total scheduled</span>
                                  <strong>{money(scheduledMinor, loan.currency_code)}</strong>
                                </div>
                                <div>
                                  <span>Confirmed repayments</span>
                                  <strong>{money(paidMinor, loan.currency_code)}</strong>
                                </div>
                                <div className="loans-balance-remaining">
                                  <span>Outstanding balance</span>
                                  <strong>{money(outstandingMinor, loan.currency_code)}</strong>
                                </div>
                              </div>
                            );
                          })()}

                          <h4>Repayment schedule</h4>
                          {details.installments?.length ? (
                            <div className="loans-installments">
                              {details.installments.map((item) => (
                                <div className="loans-installment" key={item.id}>
                                  <span>#{item.installment_number}</span>
                                  <span>{item.due_date}</span>
                                  <strong>{money(item.amount_minor, loan.currency_code)}</strong>
                                  <small>{item.status.replace("_", " ")}</small>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="loans-muted">
                              No repayment schedule yet. Activate this draft to generate one.
                            </p>
                          )}

                          <h4>Repayment history</h4>
                          {details.payments?.length ? (
                            <div className="loans-payment-list">
                              {details.payments.map((payment) => {
                                const eligibleAccounts = paymentAccounts.filter((account) => (
                                  account.is_active &&
                                  account.currency === loan.currency_code &&
                                  ["cash", "bank", "mobile_money"].includes(account.account_type)
                                ));

                                return (
                                  <div className="loans-payment-item" key={payment.id}>
                                    <div className="loans-payment-summary">
                                      <strong>{money(payment.amount_minor, loan.currency_code)}</strong>
                                      <span>{payment.payment_date}</span>
                                      <span>{payment.payment_method.replace("_", " ")}</span>
                                      <small className={`loans-payment-status status-${payment.status}`}>
                                        {payment.status}
                                      </small>
                                    </div>

                                    {payment.reference && (
                                      <p className="loans-muted">Reference: {payment.reference}</p>
                                    )}
                                    {payment.notes && (
                                      <p className="loans-muted">{payment.notes}</p>
                                    )}

                                    {payment.status === "pending" && (
                                      <div className="loans-confirm-payment">
                                        {editingPaymentId === payment.id ? (
                                          <form
                                            className="loans-repayment-form"
                                            onSubmit={(event) => {
                                              event.preventDefault();
                                              handleUpdateLoanPayment(loan, payment);
                                            }}
                                          >
                                            <h4>Edit pending repayment</h4>

                                            <label>
                                              Amount ({loan.currency_code})
                                              <input
                                                type="number"
                                                min={digitsFor(loan.currency_code) === 0 ? "1" : "0.01"}
                                                step={digitsFor(loan.currency_code) === 0 ? "1" : "0.01"}
                                                required
                                                value={paymentEditForm.amount}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  amount: event.target.value,
                                                }))}
                                              />
                                            </label>

                                            <label>
                                              Payment date
                                              <input
                                                type="date"
                                                required
                                                value={paymentEditForm.payment_date}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  payment_date: event.target.value,
                                                }))}
                                              />
                                            </label>

                                            <label>
                                              Payment source
                                              <select
                                                value={paymentEditForm.payment_source}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  payment_source: event.target.value,
                                                }))}
                                              >
                                                <option value="registered">Registered PesaPilot account</option>
                                                <option value="external">Other / external payment</option>
                                              </select>
                                            </label>

                                            <label>
                                              Payment method
                                              <select
                                                value={paymentEditForm.payment_method}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  payment_method: event.target.value,
                                                }))}
                                              >
                                                <option value="cash">Cash</option>
                                                <option value="bank">Bank</option>
                                                <option value="mobile_money">Mobile money</option>
                                                <option value="other">Other</option>
                                              </select>
                                            </label>

                                            <label>
                                              Payment reference (required)
                                              <input
                                                type="text"
                                                required
                                                maxLength={120}
                                                value={paymentEditForm.reference}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  reference: event.target.value,
                                                }))}
                                                placeholder="Transaction reference"
                                              />
                                            </label>

                                            <label>
                                              Notes (optional)
                                              <textarea
                                                rows={2}
                                                maxLength={2000}
                                                value={paymentEditForm.notes}
                                                onChange={(event) => setPaymentEditForm((current) => ({
                                                  ...current,
                                                  notes: event.target.value,
                                                }))}
                                                placeholder="Additional details"
                                              />
                                            </label>

                                            <div className="loans-modal-actions">
                                              <button
                                                type="button"
                                                className="loans-secondary-button"
                                                onClick={() => setEditingPaymentId(null)}
                                                disabled={updatingPayment === payment.id}
                                              >
                                                Cancel
                                              </button>
                                              <button
                                                type="submit"
                                                className="loans-primary-button"
                                                disabled={updatingPayment === payment.id}
                                              >
                                                {updatingPayment === payment.id ? "Saving..." : "Save changes"}
                                              </button>
                                            </div>
                                          </form>
                                        ) : (
                                          <>
                                            <div className="loans-modal-actions">
                                              <button
                                                type="button"
                                                className="loans-secondary-button"
                                                onClick={() => openEditPayment(payment, loan.currency_code)}
                                                disabled={deletingPayment === payment.id || updatingPayment === payment.id}
                                              >
                                                Edit
                                              </button>
                                              <button
                                                type="button"
                                                className="loans-secondary-button"
                                                onClick={() => handleDeleteLoanPayment(loan, payment)}
                                                disabled={deletingPayment === payment.id || updatingPayment === payment.id}
                                              >
                                                {deletingPayment === payment.id ? "Deleting..." : "Delete"}
                                              </button>
                                            </div>

                                            {payment.payment_source === "external" ? (
                                              <p className="loans-muted">
                                                External payment. No registered PesaPilot account will be used.
                                              </p>
                                            ) : (
                                              <>
                                                <label htmlFor={`payment-account-${payment.id}`}>
                                                  {loan.direction === "borrowed" ? "Pay from" : "Receive into"}
                                                </label>
                                                <select
                                                  id={`payment-account-${payment.id}`}
                                                  value={
                                                    eligibleAccounts.some(
                                                      (account) => account.id === paymentAccountSelections[payment.id]
                                                    )
                                                      ? paymentAccountSelections[payment.id]
                                                      : eligibleAccounts[0]?.id || ""
                                                  }
                                                  onChange={(event) => setPaymentAccountSelections((current) => ({
                                                    ...current,
                                                    [payment.id]: event.target.value,
                                                  }))}
                                                >
                                                  <option value="">Choose an account</option>
                                                  {eligibleAccounts.map((account) => (
                                                    <option key={account.id} value={account.id}>
                                                      {account.name} ({account.account_type})
                                                    </option>
                                                  ))}
                                                </select>
                                                {!eligibleAccounts.length && (
                                                  <small className="loans-muted">
                                                    No active cash, bank or mobile-money account is available in this loan currency.
                                                  </small>
                                                )}
                                              </>
                                            )}
                                            <button
                                              type="button"
                                              className="loans-primary-button"
                                              onClick={() => handleConfirmLoanPayment(loan, payment)}
                                              disabled={
                                                confirmingPayment === payment.id ||
                                                (
                                                  payment.payment_source !== "external" &&
                                                  !eligibleAccounts.length
                                                )
                                              }
                                            >
                                              {confirmingPayment === payment.id ? "Confirming..." : "Confirm repayment"}
                                            </button>
                                          </>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <p className="loans-muted">No repayments have been recorded.</p>
                          )}

                          {["active", "overdue", "restructured"].includes(loan.status) && (
                            <div className="loans-repayment-actions">
                              {repaymentLoan !== loan.id ? (
                                <button
                                  type="button"
                                  className="loans-primary-button"
                                  onClick={() => openRepaymentForm(loan)}
                                >
                                  Record repayment
                                </button>
                              ) : (
                                <form
                                  className="loans-repayment-form"
                                  onSubmit={(event) => {
                                    event.preventDefault();
                                    handleRecordLoanPayment(loan);
                                  }}
                                >
                                  <h4>Record a repayment</h4>
                                  <label>
                                    Amount ({loan.currency_code})
                                    <input
                                      type="number"
                                      min={digitsFor(loan.currency_code) === 0 ? "1" : "0.01"}
                                      step={digitsFor(loan.currency_code) === 0 ? "1" : "0.01"}
                                      required
                                      value={repaymentForm.amount}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        amount: event.target.value,
                                      }))}
                                      placeholder="Enter amount received"
                                    />
                                  </label>
                                  <label>
                                    Payment date
                                    <input
                                      type="date"
                                      required
                                      value={repaymentForm.payment_date}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        payment_date: event.target.value,
                                      }))}
                                    />
                                  </label>
                                  <label>
                                    Payment source
                                    <select
                                      value={repaymentForm.payment_source}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        payment_source: event.target.value,
                                      }))}
                                    >
                                      <option value="registered">Registered PesaPilot account</option>
                                      <option value="external">Other / external payment</option>
                                    </select>
                                  </label>
                                  <label>
                                    Payment method
                                    <select
                                      value={repaymentForm.payment_method}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        payment_method: event.target.value,
                                      }))}
                                    >
                                      <option value="cash">Cash</option>
                                      <option value="bank">Bank</option>
                                      <option value="mobile_money">Mobile money</option>
                                    </select>
                                  </label>
                                  <label>
                                    Payment reference (required)
                                    <input
                                      value={repaymentForm.reference}
                                      required
                                      maxLength={120}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        reference: event.target.value,
                                      }))}
                                      placeholder="Transaction reference"
                                    />
                                  </label>
                                  <label>
                                    Notes (optional)
                                    <textarea
                                      rows={2}
                                      maxLength={2000}
                                      value={repaymentForm.notes}
                                      onChange={(event) => setRepaymentForm((current) => ({
                                        ...current,
                                        notes: event.target.value,
                                      }))}
                                      placeholder="Additional details"
                                    />
                                  </label>
                                  <div className="loans-modal-actions">
                                    <button
                                      type="button"
                                      className="loans-secondary-button"
                                      onClick={() => setRepaymentLoan(null)}
                                      disabled={savingPayment}
                                    >
                                      Cancel
                                    </button>
                                    <button
                                      type="submit"
                                      className="loans-primary-button"
                                      disabled={savingPayment}
                                    >
                                      {savingPayment ? "Recording..." : "Record repayment"}
                                    </button>
                                  </div>
                                </form>
                              )}
                            </div>
                          )}

                          {loan.status === "draft" && (
                            <>
                              <button
                                type="button"
                                className="loans-secondary-button"
                                onClick={() => openEditLoan(loan)}
                              >
                                Edit loan
                              </button>
                              <button
                                type="button"
                                className="loans-primary-button"
                                onClick={() => handleActivate(loan.id)}
                                disabled={activating === loan.id}
                              >
                                <CheckCircle2 size={16} />
                                {activating === loan.id ? "Activating..." : "Activate loan"}
                              </button>
                            </>
                          )}
                        </>
                      ) : (
                        <p className="loans-muted">Loan details are unavailable.</p>
                      )}
                    </div>

                      </aside>

                    </div>

), document.body)}

                  </article>
              );
            })}
          </div>
        )}
      </section>

      {showForm && (
        <div className="loans-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !saving) setShowForm(false);
        }}>
          <div className="loans-modal">
            <div className="loans-modal-heading">
              <div>
                <span className="loans-eyebrow">MY LOANS</span>
                <h2>{editingLoan ? "Edit draft loan" : "Record a loan"}</h2>
                <p>
                  {editingLoan
                    ? "Update the details of this draft. Activated loans cannot be edited."
                    : "This creates a draft. You can activate it after reviewing the terms."}
                </p>
              </div>
              <button
                type="button"
                className="loans-modal-close"
                onClick={() => {
                  setShowForm(false);
                  setEditingLoan(null);
                }}
                disabled={saving}
              >
                ×
              </button>
            </div>

            <form onSubmit={handleCreate}>
              <div className="loans-form-grid">
                <label>
                  Loan direction
                  <select
                    value={form.direction}
                    onChange={(event) => updateForm("direction", event.target.value)}
                    disabled={Boolean(editingLoan)}
                  >
                    <option value="borrowed">I borrowed money</option>
                    <option value="lent">I lent money</option>
                  </select>
                </label>

                <label>
                  Loan type
                  <select
                    value={form.loan_type}
                    onChange={(event) => updateForm("loan_type", event.target.value)}
                  >
                    <option value="personal">Personal</option>
                    <option value="soft">Soft loan</option>
                    <option value="interest_free">Interest-free</option>
                    <option value="interest_bearing">Interest-bearing</option>
                    <option value="emergency">Emergency</option>
                    <option value="business">Business</option>
                  </select>
                </label>

                <label className="loans-field-full">
                  {form.direction === "borrowed" ? "Lender's name" : "Borrower's name"}
                  <input
                    value={form.counterparty_name}
                    onChange={(event) => updateForm("counterparty_name", event.target.value)}
                    maxLength={150}
                    required
                    placeholder="Enter full name"
                  />
                </label>

                <label>
                  Phone number (optional)
                  <input
                    value={form.counterparty_phone}
                    onChange={(event) => updateForm("counterparty_phone", event.target.value)}
                    maxLength={30}
                    placeholder="Phone number"
                  />
                </label>

                <label>
                  Principal ({currency})
                  <input
                    type="number"
                    min="0.01"
                    step={digitsFor(currency) === 0 ? "1" : "0.01"}
                    value={form.amount}
                    onChange={(event) => updateForm("amount", event.target.value)}
                    required
                    placeholder="0.00"
                  />
                </label>

                <label>
                  Interest method
                  <select
                    value={form.interest_method}
                    onChange={(event) => updateForm("interest_method", event.target.value)}
                    disabled={form.loan_type === "interest_free"}
                  >
                    <option value="none">No interest</option>
                    <option value="flat">Flat rate</option>
                    <option value="reducing">Reducing balance</option>
                  </select>
                </label>

                <label>
                  Annual interest rate (%)
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    step="0.01"
                    value={form.interest_rate_percent}
                    onChange={(event) => updateForm("interest_rate_percent", event.target.value)}
                    disabled={form.interest_method === "none" || form.loan_type === "interest_free"}
                  />
                </label>

                <label>
                  Repayment frequency
                  <select
                    value={form.repayment_frequency}
                    onChange={(event) => updateForm("repayment_frequency", event.target.value)}
                  >
                    <option value="weekly">Weekly</option>
                    <option value="biweekly">Every two weeks</option>
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                    <option value="one_time">One-time payment</option>
                  </select>
                </label>

                <label>
                  Number of instalments
                  <input
                    type="number"
                    min="1"
                    max="1200"
                    step="1"
                    value={form.term_count}
                    onChange={(event) => updateForm("term_count", event.target.value)}
                    required
                    disabled={form.repayment_frequency === "one_time"}
                  />
                </label>

                <label>
                  Loan start date
                  <input
                    type="date"
                    value={form.start_date}
                    onChange={(event) => updateForm("start_date", event.target.value)}
                    required
                  />
                </label>

                <label>
                  Final repayment date
                  <input
                    type="date"
                    value={form.due_date}
                    min={form.start_date}
                    onChange={(event) => updateForm("due_date", event.target.value)}
                    required
                  />
                </label>

                <label className="loans-field-full">
                  Description (optional)
                  <textarea
                    rows="3"
                    maxLength={2000}
                    value={form.description}
                    onChange={(event) => updateForm("description", event.target.value)}
                    placeholder="Add any notes about this loan..."
                  />
                </label>
              </div>

              <div className="loans-form-note">
                <Clock3 size={16} />
                Interest rates are annual. The final repayment date must match the repayment schedule.
              </div>

              <div className="loans-modal-actions">
                <button
                  type="button"
                  className="loans-secondary-button"
                  onClick={() => {
                    setShowForm(false);
                    setEditingLoan(null);
                  }}
                  disabled={saving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="loans-primary-button"
                  disabled={saving}
                >
                  {saving
                    ? "Saving..."
                    : editingLoan ? "Save changes" : "Save draft loan"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
