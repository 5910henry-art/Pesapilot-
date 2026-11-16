import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Receipt, Plus, RefreshCw, Wallet, Clock3, CheckCircle2,
  AlertCircle, CalendarDays, X, CreditCard, Eye, Search,
} from "lucide-react";

import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { financeApi } from "../api/client";
import { Skeleton } from "../components/Skeleton";

const ZERO_DECIMAL = new Set([
  "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW",
  "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
]);

function digitsFor(currency) {
  return ZERO_DECIMAL.has(currency) ? 0 : 2;
}

function money(value, currency) {
  const digits = digitsFor(currency);
  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value || 0) / (10 ** digits));
}

function toMinor(value, currency) {
  const digits = digitsFor(currency);
  const input = String(value).trim();

  if (!/^\d+(\.\d+)?$/.test(input)) {
    throw new Error("Enter a valid amount.");
  }

  const [whole, fraction = ""] = input.split(".");
  if (fraction.length > digits) {
    throw new Error(`Use no more than ${digits} decimal places.`);
  }

  const amount = BigInt(whole) * (10n ** BigInt(digits))
    + BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");

  if (amount <= 0n || amount > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("Enter a positive amount within the supported range.");
  }

  return Number(amount);
}

function today() {
  const date = new Date();
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

const initialBillForm = () => ({
  payee_name: "",
  title: "",
  description: "",
  reference: "",
  category_id: "",
  amount: "",
  bill_date: today(),
  due_date: today(),
});

const initialPaymentForm = () => ({
  financial_account_id: "",
  amount: "",
  payment_date: today(),
  reference: "",
  notes: "",
});

const STATUS_LABELS = {
  unpaid: "Unpaid",
  partially_paid: "Partially paid",
  paid: "Paid",
  overdue: "Overdue",
};

function getBillStatus(bill) {
  if (Number(bill.remaining_minor || 0) <= 0) return "paid";
  if (bill.due_date < today()) return "overdue";
  return bill.status;
}

export default function Bills() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;
  const { accounts: allAccounts } = useAccounts();
  const currency = activeWorkspace?.functional_currency
    || activeWorkspace?.currency_code
    || activeWorkspace?.currency?.code
    || "KES";

  const [bills, setBills] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [selectedBill, setSelectedBill] = useState(null);
  const [billDetails, setBillDetails] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [payingBill, setPayingBill] = useState(null);
  const [showPayment, setShowPayment] = useState(false);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [billForm, setBillForm] = useState(initialBillForm);
  const [paymentForm, setPaymentForm] = useState(initialPaymentForm);

  const loadData = useCallback(async (quiet = false) => {
    if (!workspaceId) {
      setBills([]);
      setCategories([]);
      setLoading(false);
      return;
    }

    if (quiet) setRefreshing(true);
    else setLoading(true);

    setError("");

    try {
      const [billResult, categoryResult] = await Promise.all([
        financeApi.listBills(workspaceId),
        financeApi.listExpenseCategories(workspaceId),
      ]);

      setBills(billResult.bills || []);
      setCategories((categoryResult.categories || []).filter(
        (item) => item.is_active && item.ledger_account?.id
      ));
    } catch (err) {
      setError(err.message || "Could not load bills.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const accounts = useMemo(() => allAccounts.filter(
    (account) =>
      account.is_active &&
      ["cash", "bank", "mobile_money"].includes(account.account_type) &&
      account.currency === currency
  ), [allAccounts, currency]);

  const visibleBills = useMemo(() => {
    const term = search.trim().toLowerCase();

    return bills.filter((bill) => {
      const status = getBillStatus(bill);
      const categoryName = categories.find(
        (category) => category.id === bill.category_id
      )?.name;

      const matchesFilter = filter === "all" || status === filter;
      const matchesSearch = !term || [
        bill.payee_name, bill.title, bill.reference, categoryName,
      ].some((value) => String(value || "").toLowerCase().includes(term));

      return matchesFilter && matchesSearch;
    });
  }, [bills, categories, filter, search]);

  const totals = useMemo(() => bills.reduce((result, bill) => {
    const remaining = Number(bill.remaining_minor || 0);
    if (remaining > 0) result.outstanding += remaining;
    const status = getBillStatus(bill);
    if (status === "overdue") result.overdue += remaining;
    if (status === "paid") result.paid += Number(bill.amount_minor || 0);
    return result;
  }, { outstanding: 0, overdue: 0, paid: 0 }), [bills]);

  async function openDetails(bill) {
    setSelectedBill(bill);
    setBillDetails(null);
    setDetailsLoading(true);
    setError("");

    try {
      const result = await financeApi.getBill(bill.id, workspaceId);
      setBillDetails(result);
    } catch (err) {
      setError(err.message || "Could not load bill details.");
      setSelectedBill(null);
    } finally {
      setDetailsLoading(false);
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    setError("");
    setNotice("");

    if (!billForm.category_id) {
      setError("Select an expense category.");
      return;
    }

    if (billForm.due_date < billForm.bill_date) {
      setError("The due date cannot be before the bill date.");
      return;
    }

    let amountMinor;
    try {
      amountMinor = toMinor(billForm.amount, currency);
    } catch (err) {
      setError(err.message);
      return;
    }

    setSaving(true);
    try {
      const result = await financeApi.createBill({
        workspace_id: workspaceId,
        payee_name: billForm.payee_name.trim(),
        title: billForm.title.trim(),
        description: billForm.description.trim() || null,
        reference: billForm.reference.trim() || null,
        category_id: billForm.category_id,
        amount_minor: amountMinor,
        currency_code: currency,
        bill_date: billForm.bill_date,
        due_date: billForm.due_date,
      });

      setShowCreate(false);
      setBillForm(initialBillForm());
      setNotice(result.message || "Bill created successfully.");
      await loadData(true);
    } catch (err) {
      setError(err.message || "Could not create bill.");
    } finally {
      setSaving(false);
    }
  }

  function openPayment(bill) {
    setPayingBill(bill);
    setPaymentForm({
      ...initialPaymentForm(),
      amount: (Number(bill.remaining_minor || 0) / (10 ** digitsFor(currency))).toFixed(digitsFor(currency)),
      financial_account_id: accounts[0]?.id || "",
    });
    setShowPayment(true);
    setSelectedBill(null);
    setError("");
  }

  async function handlePayment(event) {
    event.preventDefault();
    if (!payingBill) return;

    setError("");
    setNotice("");

    if (!paymentForm.financial_account_id) {
      setError("Choose the account used to pay this bill.");
      return;
    }

    let amountMinor;
    try {
      amountMinor = toMinor(paymentForm.amount, currency);
    } catch (err) {
      setError(err.message);
      return;
    }

    if (amountMinor > Number(payingBill.remaining_minor || 0)) {
      setError("Payment cannot exceed the outstanding balance.");
      return;
    }

    setSaving(true);
    try {
      const result = await financeApi.recordBillPayment(payingBill.id, {
        workspace_id: workspaceId,
        financial_account_id: paymentForm.financial_account_id,
        amount_minor: amountMinor,
        payment_date: paymentForm.payment_date,
        reference: paymentForm.reference.trim() || null,
        notes: paymentForm.notes.trim() || null,
      });

      setShowPayment(false);
      setPayingBill(null);
      setPaymentForm(initialPaymentForm());
      setNotice(result.message || "Bill payment recorded successfully.");
      await loadData(true);
    } catch (err) {
      setError(err.message || "Could not record payment.");
    } finally {
      setSaving(false);
    }
  }

  if (!workspaceId) {
    return (
      <section className="bills-page">
        <div className="panel"><p>Select a workspace to manage bills.</p></div>
      </section>
    );
  }

  return (
    <section className="bills-page">
      <div className="bills-hero">
        <div className="bills-hero-copy">
          <div className="bills-hero-kicker">
            <Receipt size={14} /> PAYMENT TRACKER
          </div>
          <h1>Stay ahead of your bills.</h1>
          <p>
            Keep every obligation organized. Track what you owe, monitor due
            dates and record payments as you settle them.
          </p>
          <div className="bills-hero-meta">
            <span><Clock3 size={15} /> Track due dates</span>
            <span><CheckCircle2 size={15} /> Monitor payments</span>
          </div>
        </div>

        <div className="bills-hero-side">
          <div className="bills-hero-art" aria-hidden="true">
            <Receipt size={39} strokeWidth={1.5} />
          </div>
          <div className="bills-hero-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={() => loadData(true)}
              disabled={refreshing}
            >
              <RefreshCw size={16} className={refreshing ? "bills-spin" : ""} />
              Refresh
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                setError("");
                setShowCreate(true);
              }}
            >
              <Plus size={17} /> New bill
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="accounts-message error">
          <AlertCircle size={17} /> {error}
        </div>
      )}
      {notice && (
        <div className="accounts-message success">
          <CheckCircle2 size={17} /> {notice}
        </div>
      )}

      <div className="bills-summary">
        <div className="bills-summary-card outstanding">
          <div className="bills-summary-icon"><Wallet size={19} /></div>
          <span>Outstanding</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : money(totals.outstanding, currency)}</strong>
          <small>Remaining across unpaid bills</small>
        </div>
        <div className="bills-summary-card overdue">
          <div className="bills-summary-icon"><Clock3 size={19} /></div>
          <span>Overdue</span>
          <strong>{loading ? <Skeleton width="125px" height={23} /> : money(totals.overdue, currency)}</strong>
          <small>Past their due date</small>
        </div>
        <div className="bills-summary-card paid">
          <div className="bills-summary-icon"><CheckCircle2 size={19} /></div>
          <span>Fully paid bills</span>
          <strong>{loading ? <Skeleton width="55px" height={23} /> : bills.filter((bill) => getBillStatus(bill) === "paid").length}</strong>
          <small>{money(totals.paid, currency)} paid in full</small>
        </div>
      </div>

      <div className="panel bills-list-panel">
        <div className="panel-heading">
          <div>
            <h2>Your bills</h2>
            <p>Manage due dates, outstanding amounts and payments.</p>
          </div>
          <span className="bills-count">{visibleBills.length} shown</span>
        </div>

        <div className="bills-toolbar">
          <div className="bills-search">
            <Search size={16} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search payee, bill or category..."
              aria-label="Search bills"
            />
          </div>
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            <option value="all">All bills</option>
            <option value="unpaid">Unpaid</option>
            <option value="partially_paid">Partially paid</option>
            <option value="overdue">Overdue</option>
            <option value="paid">Paid</option>
          </select>
        </div>

        {loading ? (
          <div className="bills-table-wrap bills-skeleton-wrap" aria-hidden="true">
            <table className="bills-table">
              <thead>
                <tr>
                  <th>Bill</th>
                  <th>Due date</th>
                  <th>Amount</th>
                  <th>Remaining</th>
                  <th>Status</th>
                  <th aria-label="Actions"></th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 5 }, (_, index) => (
                  <tr key={index}>
                    <td>
                      <div className="bills-skeleton-cell">
                        <Skeleton width="68%" height={14} />
                        <Skeleton width="48%" height={10} />
                      </div>
                    </td>
                    <td><Skeleton width="85px" height={12} /></td>
                    <td><Skeleton width="75px" height={12} /></td>
                    <td><Skeleton width="75px" height={12} /></td>
                    <td><Skeleton width="68px" height={21} radius={20} /></td>
                    <td><Skeleton width="35px" height={28} radius={8} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : visibleBills.length === 0 ? (
          <div className="bills-empty">
            <Receipt size={36} />
            <h3>{bills.length ? "No matching bills" : "No bills yet"}</h3>
            <p>
              {bills.length
                ? "Try changing your search or status filter."
                : "Add your rent, utilities, school fees or other obligations."}
            </p>
            {!bills.length && (
              <button className="primary-button" onClick={() => setShowCreate(true)}>
                <Plus size={16} /> Create your first bill
              </button>
            )}
          </div>
        ) : (
          <div className="bills-table-wrap">
            <table className="bills-table">
              <thead>
                <tr>
                  <th>Bill</th>
                  <th>Due date</th>
                  <th>Amount</th>
                  <th>Remaining</th>
                  <th>Status</th>
                  <th aria-label="Actions"></th>
                </tr>
              </thead>
              <tbody>
                {visibleBills.map((bill) => (
                  <tr
                    key={bill.id}
                    className={`bills-row bills-row-${getBillStatus(bill)}`}
                  >
                    <td>
                      <div className="bills-name">{bill.title}</div>
                      <div className="bills-subtext">
                        {bill.payee_name} · {categories.find((category) => category.id === bill.category_id)?.name || "Expense"}
                      </div>
                    </td>
                    <td>
                      <span className={`bills-date bills-date-${getBillStatus(bill)}`}>
                        <CalendarDays size={14} /> {bill.due_date}
                      </span>
                    </td>
                    <td>{money(bill.amount_minor, bill.currency_code)}</td>
                    <td className="bills-remaining">
                      {money(bill.remaining_minor, bill.currency_code)}
                    </td>
                    <td>
                      <span className={`bills-status ${getBillStatus(bill)}`}>
                        {STATUS_LABELS[getBillStatus(bill)] || getBillStatus(bill)}
                      </span>
                    </td>
                    <td>
                      <div className="bills-row-actions">
                        <button
                          type="button"
                          className="bills-icon-button"
                          title="View details"
                          onClick={() => openDetails(bill)}
                        >
                          <Eye size={16} />
                        </button>
                        {bill.remaining_minor > 0 && (
                          <button
                            type="button"
                            className="bills-pay-button"
                            onClick={() => openPayment(bill)}
                          >
                            Pay
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && (
        <div className="bills-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !saving) setShowCreate(false);
        }}>
          <div className="bills-modal">
            <div className="bills-modal-heading">
              <div>
                <h2>Create a bill</h2>
                <p>Record an amount you owe and when it needs to be paid.</p>
              </div>
              <button className="bills-modal-close" onClick={() => setShowCreate(false)} type="button">
                <X size={19} />
              </button>
            </div>

            <form onSubmit={handleCreate}>
              <div className="bills-form-grid">
                <label>
                  Payee / Who do you owe?
                  <input
                    value={billForm.payee_name}
                    onChange={(e) => setBillForm({ ...billForm, payee_name: e.target.value })}
                    placeholder="e.g. Kenya Power"
                    maxLength={150}
                    required
                  />
                </label>
                <label>
                  Bill title
                  <input
                    value={billForm.title}
                    onChange={(e) => setBillForm({ ...billForm, title: e.target.value })}
                    placeholder="e.g. November electricity"
                    maxLength={150}
                    required
                  />
                </label>
                <label>
                  Expense category
                  <select
                    value={billForm.category_id}
                    onChange={(e) => setBillForm({ ...billForm, category_id: e.target.value })}
                    required
                  >
                    <option value="">Select category</option>
                    {categories.map((category) => (
                      <option key={category.id} value={category.id}>{category.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Amount ({currency})
                  <input
                    type="number"
                    min="0"
                    step={digitsFor(currency) === 0 ? "1" : "0.01"}
                    value={billForm.amount}
                    onChange={(e) => setBillForm({ ...billForm, amount: e.target.value })}
                    placeholder="e.g. 3500"
                    required
                  />
                </label>
                <label>
                  Bill date
                  <input
                    type="date"
                    value={billForm.bill_date}
                    onChange={(e) => setBillForm({ ...billForm, bill_date: e.target.value })}
                    required
                  />
                </label>
                <label>
                  Due date
                  <input
                    type="date"
                    min={billForm.bill_date}
                    value={billForm.due_date}
                    onChange={(e) => setBillForm({ ...billForm, due_date: e.target.value })}
                    required
                  />
                </label>
                <label>
                  Reference (optional)
                  <input
                    value={billForm.reference}
                    onChange={(e) => setBillForm({ ...billForm, reference: e.target.value })}
                    placeholder="Invoice or account number"
                    maxLength={100}
                  />
                </label>
                <label className="bills-field-full">
                  Description (optional)
                  <textarea
                    value={billForm.description}
                    onChange={(e) => setBillForm({ ...billForm, description: e.target.value })}
                    placeholder="Additional details..."
                    rows={3}
                  />
                </label>
              </div>

              {!categories.length && (
                <p className="bills-form-note">
                  Create an active expense category in Budgets before recording a bill.
                </p>
              )}
              <div className="bills-modal-actions">
                <button type="button" className="secondary-button" onClick={() => setShowCreate(false)}>
                  Cancel
                </button>
                <button className="primary-button" disabled={saving || !categories.length}>
                  {saving ? "Creating..." : "Create bill"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showPayment && payingBill && (
        <div className="bills-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !saving) setShowPayment(false);
        }}>
          <div className="bills-modal">
            <div className="bills-modal-heading">
              <div>
                <h2>Pay bill</h2>
                <p>{payingBill.title} · {payingBill.payee_name}</p>
              </div>
              <button className="bills-modal-close" onClick={() => setShowPayment(false)} type="button">
                <X size={19} />
              </button>
            </div>

            <div className="bills-payment-summary">
              <span>Outstanding balance</span>
              <strong>{money(payingBill.remaining_minor, currency)}</strong>
            </div>

            <form onSubmit={handlePayment}>
              <div className="bills-form-grid">
                <label className="bills-field-full">
                  Pay from account
                  <select
                    value={paymentForm.financial_account_id}
                    onChange={(e) => setPaymentForm({ ...paymentForm, financial_account_id: e.target.value })}
                    required
                  >
                    <option value="">Select account</option>
                    {accounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name} · {account.account_type.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Payment amount ({currency})
                  <input
                    type="number"
                    min="0"
                    max={Number(payingBill.remaining_minor) / (10 ** digitsFor(currency))}
                    step={digitsFor(currency) === 0 ? "1" : "0.01"}
                    value={paymentForm.amount}
                    onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
                    required
                  />
                </label>
                <label>
                  Payment date
                  <input
                    type="date"
                    value={paymentForm.payment_date}
                    onChange={(e) => setPaymentForm({ ...paymentForm, payment_date: e.target.value })}
                    required
                  />
                </label>
                <label>
                  Reference (optional)
                  <input
                    value={paymentForm.reference}
                    onChange={(e) => setPaymentForm({ ...paymentForm, reference: e.target.value })}
                    placeholder="M-PESA code, receipt..."
                    maxLength={100}
                  />
                </label>
                <label>
                  Notes (optional)
                  <input
                    value={paymentForm.notes}
                    onChange={(e) => setPaymentForm({ ...paymentForm, notes: e.target.value })}
                    placeholder="Payment details"
                  />
                </label>
              </div>

              {!accounts.length && (
                <p className="bills-form-note">
                  Add an active cash, bank or mobile-money account in Accounts using {currency}.
                </p>
              )}
              <div className="bills-modal-actions">
                <button type="button" className="secondary-button" onClick={() => setShowPayment(false)}>
                  Cancel
                </button>
                <button className="primary-button" disabled={saving || !accounts.length}>
                  {saving ? "Recording..." : "Record payment"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {selectedBill && (
        <div className="bills-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSelectedBill(null);
        }}>
          <div className="bills-modal">
            <div className="bills-modal-heading">
              <div>
                <h2>{selectedBill.title}</h2>
                <p>{selectedBill.payee_name}</p>
              </div>
              <button className="bills-modal-close" onClick={() => setSelectedBill(null)} type="button">
                <X size={19} />
              </button>
            </div>

            {detailsLoading ? (
              <div className="bills-detail-grid bills-detail-skeleton" aria-hidden="true">
                {Array.from({ length: 4 }, (_, index) => (
                  <div key={index}>
                    <Skeleton width="65%" height={11} />
                    <Skeleton width="85%" height={17} />
                  </div>
                ))}
              </div>
            ) : billDetails ? (
              <>
                <div className="bills-detail-grid">
                  <div><span>Bill amount</span><strong>{money(billDetails.bill?.amount_minor, currency)}</strong></div>
                  <div><span>Paid so far</span><strong>{money(billDetails.bill?.amount_paid_minor, currency)}</strong></div>
                  <div><span>Remaining</span><strong>{money(billDetails.bill?.remaining_minor, currency)}</strong></div>
                  <div><span>Due date</span><strong>{billDetails.bill?.due_date}</strong></div>
                </div>

                <div className="bills-history-heading">
                  <h3>Payment history</h3>
                  <span>{(billDetails.bill?.payments || []).length} payments</span>
                </div>
                {(billDetails.bill?.payments || []).length ? (
                  <div className="bills-history">
                    {billDetails.bill.payments.map((payment) => {
                      const reversed = payment.status === "reversed";

                      return (
                        <div
                          className={`bills-history-row${reversed ? " is-reversed" : ""}`}
                          key={payment.id}
                        >
                          <div>
                            <strong>{money(payment.amount_minor, payment.currency_code)}</strong>
                            <span>{payment.payment_date} · {accounts.find((account) => account.id === payment.financial_account_id)?.name || "Account"}</span>
                            {payment.reference && <small>Ref: {payment.reference}</small>}
                            <small className="bills-payment-status">
                              {reversed ? "Reversed" : "Paid"}
                            </small>
                          </div>
                          {reversed ? (
                            <X size={17} />
                          ) : (
                            <CheckCircle2 size={17} />
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="bills-no-history">No payments have been recorded yet.</p>
                )}

                {Number(billDetails.bill?.remaining_minor) > 0 && (
                  <div className="bills-modal-actions">
                    <button className="primary-button" onClick={() => openPayment(billDetails.bill)}>
                      <CreditCard size={16} /> Record payment
                    </button>
                  </div>
                )}
              </>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
