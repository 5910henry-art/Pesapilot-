import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Wallet, CheckCircle, X } from "lucide-react";
import { platformOwnerApi } from "../api/client";
import { useToast } from "../context/ToastContext";

const ZERO_DECIMAL = new Set([
  "JPY", "UGX", "RWF", "BIF", "DJF", "GNF", "KMF", "XAF", "XOF",
]);

function money(minor, currency = "KES") {
  const zero = ZERO_DECIMAL.has(currency);
  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    maximumFractionDigits: zero ? 0 : 2,
  }).format(Number(minor || 0) / (zero ? 1 : 100));
}

function dateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function ActionModal({ payment, accounts, mode, onClose, onSubmit, saving }) {
  const [accountId, setAccountId] = useState("");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");

  const availableAccounts = accounts.filter(
    (account) => account.currency_code === payment.currency_code
  );

  async function submit(event) {
    event.preventDefault();

    if (mode === "confirm") {
      if (!accountId) return;
      await onSubmit(payment.id, {
        financial_account_id: accountId,
      });
    } else if (mode === "reject") {
      if (!reason.trim()) return;
      await onSubmit(payment.id, {
        reason: reason.trim(),
      });
    } else {
      if (!reference.trim()) return;
      await onSubmit(payment.id, {
        reference: reference.trim(),
      });
    }
  }

  return (
    <div
      className="platform-owner-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="platform-owner-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="collection-action-title"
      >
        <div className="platform-owner-modal-header">
          <div>
            <h2 id="collection-action-title">
              {mode === "confirm"
                ? "Confirm repayment"
                : mode === "reject"
                  ? "Reject repayment report"
                  : "Reverse repayment"}
            </h2>
            <p>{payment.customer_email}</p>
          </div>
          <button
            type="button"
            className="platform-owner-icon-button"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="platform-owner-review-summary">
          <div>
            <span>Amount</span>
            <strong>{money(payment.amount_minor, payment.currency_code)}</strong>
          </div>
          <div>
            <span>Payment date</span>
            <strong>{dateTime(payment.payment_date)}</strong>
          </div>
          <div>
            <span>Method</span>
            <strong>{payment.payment_method || "—"}</strong>
          </div>
          <div>
            <span>Paid from</span>
            <strong>{payment.borrower_account_name || "Other / not listed"}</strong>
          </div>
          <div>
            <span>Reference</span>
            <strong>{payment.reference || "—"}</strong>
          </div>
        </div>

        <form className="platform-owner-form" onSubmit={submit}>
          {mode === "confirm" ? (
            <label>
              Platform receiving account
              <select
                required
                value={accountId}
                onChange={(event) => setAccountId(event.target.value)}
              >
                <option value="">Select an account</option>
                {availableAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.account_type} · {account.currency_code}
                  </option>
                ))}
              </select>
              <small>
                Only active platform accounts in {payment.currency_code} are eligible.
              </small>
              {availableAccounts.length === 0 && (
                <small>No eligible receiving account was found for this currency.</small>
              )}
            </label>
          ) : mode === "reject" ? (
            <label>
              Rejection reason
              <textarea
                required
                maxLength={500}
                rows={4}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Explain why this reported repayment is being rejected"
              />
              <small>
                This rejects the report only. No money or loan balance will be changed.
              </small>
            </label>
          ) : (
            <label>
              Reversal reference
              <input
                required
                maxLength={100}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="Enter the reason or reversal reference"
              />
              <small>The original repayment will be reversed in the accounting records.</small>
            </label>
          )}

          <div className="platform-owner-modal-actions">
            <button
              type="button"
              className="platform-owner-button secondary"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="platform-owner-button primary"
              disabled={
                saving ||
                (mode === "confirm" && availableAccounts.length === 0) ||
                (mode === "reject" && !reason.trim()) ||
                (mode === "reverse" && !reference.trim())
              }
            >
              {saving
                ? "Processing..."
                : mode === "confirm"
                  ? "Confirm repayment"
                  : mode === "reject"
                    ? "Reject reported repayment"
                    : "Reverse repayment"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function LateFeeActionModal({ payment, accounts, mode, onClose, onSubmit, saving }) {
  const [accountId, setAccountId] = useState("");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");

  const availableAccounts = accounts.filter(
    (account) => account.currency_code === payment.currency_code
  );

  async function submit(event) {
    event.preventDefault();
    if (mode === "confirm") {
      if (accountId) await onSubmit(payment.id, { financial_account_id: accountId });
    } else if (mode === "reject") {
      if (reason.trim()) await onSubmit(payment.id, { reason: reason.trim() });
    } else if (reference.trim()) {
      await onSubmit(payment.id, { reference: reference.trim() });
    }
  }

  const title = mode === "confirm"
    ? "Confirm late-fee payment"
    : mode === "reject"
      ? "Reject late-fee payment"
      : "Reverse late-fee payment";

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !saving) onClose();
    }}>
      <section className="platform-owner-modal" role="dialog" aria-modal="true"
        aria-labelledby="late-fee-action-title">
        <div className="platform-owner-modal-header">
          <div>
            <h2 id="late-fee-action-title">{title}</h2>
            <p>{payment.customer_email}</p>
          </div>
          <button type="button" className="platform-owner-icon-button"
            onClick={onClose} aria-label="Close" disabled={saving}>
            <X size={18} />
          </button>
        </div>

        <div className="platform-owner-review-summary">
          <div><span>Amount</span><strong>{money(payment.amount_minor, payment.currency_code)}</strong></div>
          <div><span>Payment date</span><strong>{dateTime(payment.payment_date)}</strong></div>
          <div><span>Method</span><strong>{payment.payment_method || "—"}</strong></div>
          <div><span>Reference</span><strong>{payment.reference || "—"}</strong></div>
          <div><span>Loan</span><strong>{payment.loan_id.slice(0, 8)}…</strong></div>
        </div>

        <form className="platform-owner-form" onSubmit={submit}>
          {mode === "confirm" ? (
            <label>
              Platform receiving account
              <select required value={accountId} onChange={(event) => setAccountId(event.target.value)}>
                <option value="">Select an account</option>
                {availableAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.account_type} · {account.currency_code}
                  </option>
                ))}
              </select>
              <small>Only active platform accounts in {payment.currency_code} are eligible.</small>
              {availableAccounts.length === 0 && (
                <small>No eligible receiving account was found for this currency.</small>
              )}
            </label>
          ) : mode === "reject" ? (
            <label>
              Rejection reason
              <textarea required maxLength={500} rows={4} value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Explain why this fee payment report is being rejected" />
            </label>
          ) : (
            <label>
              Reversal reference
              <input required maxLength={100} value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="Enter the reason or reversal reference" />
              <small>The fee receipt accounting entry will be reversed and fee balances restored.</small>
            </label>
          )}

          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary"
              onClick={onClose} disabled={saving}>Cancel</button>
            <button type="submit" className="platform-owner-button primary"
              disabled={saving ||
                (mode === "confirm" && availableAccounts.length === 0) ||
                (mode === "reject" && !reason.trim()) ||
                (mode === "reverse" && !reference.trim())}>
              {saving ? "Processing..." : title}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function PlatformOwnerCollections() {
  const [pending, setPending] = useState([]);
  const [pendingFees, setPendingFees] = useState([]);
  const [confirmedFees, setConfirmedFees] = useState([]);
  const [overdue, setOverdue] = useState([]);
  const [selectedFee, setSelectedFee] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { showToast } = useToast();
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("pending");
  const [asOfDate, setAsOfDate] = useState("");

  const loadCollections = useCallback(async () => {
    setLoading(true);

    try {
      const [collections, financialAccounts] = await Promise.all([
        platformOwnerApi.listCollections({ page: 1, per_page: 100 }),
        platformOwnerApi.listFinancialAccounts(),
      ]);

      setPending(collections.pending_payments || []);
      setPendingFees(collections.pending_late_fee_payments || []);
      setConfirmedFees(collections.confirmed_late_fee_payments || []);
      setOverdue(collections.overdue_installments || []);
      setAsOfDate(collections.as_of_date || "");
      setAccounts(financialAccounts.accounts || []);
    } catch (err) {
      showToast(err.message || "Collections could not be loaded.", { type: "error" });
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadCollections();
  }, [loadCollections]);

  async function processPayment(paymentId, payload) {
    if (!selected) return;

    setSaving(true);

    try {
      if (selected.mode === "confirm") {
        await platformOwnerApi.confirmPayment(paymentId, payload);
        showToast("Repayment confirmed and posted successfully.", { type: "success" });
      } else if (selected.mode === "reject") {
        await platformOwnerApi.rejectPayment(paymentId, payload);
        showToast("Reported repayment rejected. No accounting entry was created.", { type: "info" });
      } else {
        await platformOwnerApi.reversePayment(paymentId, payload);
        showToast("Repayment reversed successfully.", { type: "success" });
      }

      setSelected(null);
      await loadCollections();
    } catch (err) {
      showToast(err.message || "The repayment operation failed.", { type: "error" });
    } finally {
      setSaving(false);
    }
  }

  async function processLateFeePayment(paymentId, payload) {
    if (!selectedFee) return;

    setSaving(true);

    try {
      if (selectedFee.mode === "confirm") {
        await platformOwnerApi.confirmLateFeePayment(paymentId, payload);
        showToast("Late-fee payment confirmed and posted successfully.", { type: "success" });
      } else if (selectedFee.mode === "reject") {
        await platformOwnerApi.rejectLateFeePayment(paymentId, payload);
        showToast("Late-fee payment report rejected.", { type: "info" });
      } else {
        await platformOwnerApi.reverseLateFeePayment(paymentId, payload);
        showToast("Late-fee payment reversed. Fee balances restored.", { type: "success" });
      }

      setSelectedFee(null);
      await loadCollections();
    } catch (err) {
      showToast(err.message || "The late-fee payment operation failed.", { type: "error" });
    } finally {
      setSaving(false);
    }
  }

  const visiblePending = pending;
  const visibleFees = pendingFees;
  const visibleConfirmedFees = confirmedFees;
  const visibleOverdue = overdue;

  return (
    <div className="platform-owner-page platform-owner-collections-page">
      <div className="platform-owner-page-header">
        <div>
          <span className="platform-owner-eyebrow">LENDING OPERATIONS</span>
          <h1>Collections</h1>
          <p>Review reported repayments, confirm receipts and monitor overdue installments.</p>
        </div>

        <button
          type="button"
          className="platform-owner-button secondary"
          onClick={loadCollections}
          disabled={loading}
        >
          <RefreshCw size={15} className={loading ? "platform-owner-spin" : ""} />
          Refresh
        </button>
      </div>


      <div className="platform-owner-stat-row">
        <div className="platform-owner-stat-card">
          <span>Pending repayments</span>
          <strong>{pending.length}</strong>
        </div>
        <div className="platform-owner-stat-card">
          <span>Pending late-fee payments</span>
          <strong>{pendingFees.length}</strong>
        </div>
        <div className="platform-owner-stat-card">
          <span>Overdue installments</span>
          <strong>{overdue.length}</strong>
        </div>
        <div className="platform-owner-stat-card">
          <span>As of date</span>
          <strong>{asOfDate || "—"}</strong>
        </div>
      </div>

      <div className="platform-owner-toolbar">
        <div className="platform-owner-filter-tabs">
          <button
            type="button"
            className={filter === "pending" ? "active" : ""}
            onClick={() => setFilter("pending")}
          >
            Pending repayments
          </button>
          <button
            type="button"
            className={filter === "fees" ? "active" : ""}
            onClick={() => setFilter("fees")}
          >
            Late-fee payments ({pendingFees.length})
          </button>
          <button
            type="button"
            className={filter === "confirmedFees" ? "active" : ""}
            onClick={() => setFilter("confirmedFees")}
          >
            Confirmed fee receipts ({confirmedFees.length})
          </button>
          <button
            type="button"
            className={filter === "overdue" ? "active" : ""}
            onClick={() => setFilter("overdue")}
          >
            Overdue installments
          </button>
        </div>
      </div>

      {loading ? (
        <div className="platform-owner-empty">Loading collections...</div>
      ) : filter === "pending" ? (
        visiblePending.length === 0 ? (
          <div className="platform-owner-empty">
            <Wallet size={30} />
            <h3>No pending repayments</h3>
            <p>Customer-reported repayments awaiting confirmation will appear here.</p>
          </div>
        ) : (
          <div className="platform-owner-table-wrap">
            <table className="platform-owner-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Loan</th>
                  <th>Amount</th>
                  <th>Payment date</th>
                  <th>Method</th>
                  <th>Paid from</th>
                  <th>Reference</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visiblePending.map((payment) => (
                  <tr key={payment.id}>
                    <td>{payment.customer_email}</td>
                    <td>{payment.loan_id.slice(0, 8)}…</td>
                    <td>{money(payment.amount_minor, payment.currency_code)}</td>
                    <td>{dateTime(payment.payment_date)}</td>
                    <td>{payment.payment_method || "—"}</td>
                    <td>{payment.borrower_account_name || "Other / not listed"}</td>
                    <td>{payment.reference || "—"}</td>
                    <td>
                      <div className="platform-owner-inline-actions">
                        <button
                          type="button"
                          className="platform-owner-button primary"
                          onClick={() => {
                            setSelected({ payment, mode: "confirm" });
                          }}
                        >
                          <CheckCircle size={14} /> Confirm
                        </button>
                        <button
                          type="button"
                          className="platform-owner-button secondary"
                          onClick={() => {
                            setSelected({ payment, mode: "reject" });
                          }}
                        >
                          <X size={14} /> Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : filter === "fees" ? (
        visibleFees.length === 0 ? (
          <div className="platform-owner-empty">
            <CheckCircle size={30} />
            <h3>No pending late-fee payments</h3>
            <p>Customer-reported late-fee payments awaiting review will appear here.</p>
          </div>
        ) : (
          <div className="platform-owner-table-wrap">
            <table className="platform-owner-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Loan</th>
                  <th>Amount</th>
                  <th>Payment date</th>
                  <th>Method</th>
                  <th>Reference</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleFees.map((payment) => (
                  <tr key={payment.id}>
                    <td>{payment.customer_email}</td>
                    <td>{payment.loan_id.slice(0, 8)}…</td>
                    <td>{money(payment.amount_minor, payment.currency_code)}</td>
                    <td>{dateTime(payment.payment_date)}</td>
                    <td>{payment.payment_method || "—"}</td>
                    <td>{payment.reference || "—"}</td>
                    <td>
                      <div className="platform-owner-inline-actions">
                        <button type="button" className="platform-owner-button primary"
                          onClick={() => {
                            setSelectedFee({ payment, mode: "confirm" });
                          }}>
                          <CheckCircle size={14} /> Confirm
                        </button>
                        <button type="button" className="platform-owner-button secondary"
                          onClick={() => {
                            setSelectedFee({ payment, mode: "reject" });
                          }}>
                          <X size={14} /> Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : filter === "confirmedFees" ? (
        visibleConfirmedFees.length === 0 ? (
          <div className="platform-owner-empty">
            <CheckCircle size={30} />
            <h3>No confirmed late-fee receipts</h3>
            <p>Confirmed fee receipts available for reversal will appear here.</p>
          </div>
        ) : (
          <div className="platform-owner-table-wrap">
            <table className="platform-owner-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Loan</th>
                  <th>Amount</th>
                  <th>Payment date</th>
                  <th>Confirmed</th>
                  <th>Method</th>
                  <th>Reference</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleConfirmedFees.map((payment) => (
                  <tr key={payment.id}>
                    <td>{payment.customer_email}</td>
                    <td>{payment.loan_id.slice(0, 8)}…</td>
                    <td>{money(payment.amount_minor, payment.currency_code)}</td>
                    <td>{dateTime(payment.payment_date)}</td>
                    <td>{dateTime(payment.confirmed_at)}</td>
                    <td>{payment.payment_method || "—"}</td>
                    <td>{payment.reference || "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="platform-owner-button secondary"
                        onClick={() => {
                          setSelectedFee({ payment, mode: "reverse" });
                        }}
                      >
                        Reverse receipt
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : visibleOverdue.length === 0 ? (
        <div className="platform-owner-empty">
          <CheckCircle size={30} />
          <h3>No overdue installments</h3>
          <p>There are no overdue unpaid installments in the current results.</p>
        </div>
      ) : (
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Loan</th>
                <th>Installment</th>
                <th>Due date</th>
                <th>Amount</th>
                <th>Paid</th>
                <th>Remaining</th>
                <th>Days overdue</th>
                <th>Late fee</th>
                <th>Fee paid</th>
                <th>Fee outstanding</th>
              </tr>
            </thead>
            <tbody>
              {visibleOverdue.map((item) => (
                <tr key={item.id}>
                  <td>{item.customer_email}</td>
                  <td>{item.loan_id.slice(0, 8)}…</td>
                  <td>#{item.installment_number}</td>
                  <td>{item.due_date}</td>
                  <td>{money(item.amount_minor, item.currency_code)}</td>
                  <td>{money(item.paid_minor, item.currency_code)}</td>
                  <td>{money(item.remaining_minor, item.currency_code)}</td>
                  <td>{Number(item.days_overdue || 0)}</td>
                  <td>{money(item.late_fee_amount_minor, item.currency_code)}</td>
                  <td>{money(item.late_fee_paid_minor, item.currency_code)}</td>
                  <td>{money(item.late_fee_outstanding_minor, item.currency_code)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedFee && (
        <LateFeeActionModal
          payment={selectedFee.payment}
          accounts={accounts}
          mode={selectedFee.mode}
          onClose={() => {
            if (!saving) {
              setSelectedFee(null);
            }
          }}
          onSubmit={processLateFeePayment}
          saving={saving}
        />
      )}

      {selected && (
        <ActionModal
          payment={selected.payment}
          accounts={accounts}
          mode={selected.mode}
          onClose={() => {
            if (!saving) {
              setSelected(null);
            }
          }}
          onSubmit={processPayment}
          saving={saving}
        />
      )}
    </div>
  );
}
