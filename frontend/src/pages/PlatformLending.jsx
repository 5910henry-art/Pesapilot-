import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Landmark, RefreshCw, Plus, ChevronRight, ArrowLeft,
  Clock3, CheckCircle2, AlertCircle, X, Wallet,
  FileText, CalendarDays, HandCoins, Send,
} from "lucide-react";
import { platformLendingApi } from "../api/client";
import { Skeleton } from "../components/Skeleton";

const ZERO_DECIMAL = new Set([
  "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW",
  "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
]);

function digitsFor(currency) {
  return ZERO_DECIMAL.has(currency) ? 0 : 2;
}

function money(minor, currency = "KES") {
  const digits = digitsFor(currency);
  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(minor || 0) / (10 ** digits));
}

function toMinorUnits(value, currency) {
  const digits = digitsFor(currency);
  const input = String(value).trim();

  if (!/^\d+(\.\d+)?$/.test(input)) {
    throw new Error("Enter a valid amount.");
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

function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-KE", {
        day: "numeric", month: "short", year: "numeric",
      });
}

function statusLabel(status) {
  return String(status || "unknown")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function Status({ value }) {
  const tone = ["paid", "approved", "confirmed", "active"].includes(value)
    ? "success"
    : ["pending", "partially_paid", "overdue"].includes(value)
      ? "warning"
      : ["rejected", "failed", "reversed", "cancelled"].includes(value)
        ? "danger"
        : "neutral";

  return (
    <span className={`lending-status lending-status-${tone}`}>
      {statusLabel(value)}
    </span>
  );
}

function Field({ label, children }) {
  return (
    <label className="lending-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export default function PlatformLending() {
  const [products, setProducts] = useState([]);
  const [applications, setApplications] = useState([]);
  const [loans, setLoans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("products");
  const [selectedLoan, setSelectedLoan] = useState(null);
  const [loanDetails, setLoanDetails] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const [showApply, setShowApply] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [applicationForm, setApplicationForm] = useState({
    amount: "", term_count: "", purpose: "",
  });
  const [savingApplication, setSavingApplication] = useState(false);
  const [withdrawing, setWithdrawing] = useState(null);

  const [showRepayment, setShowRepayment] = useState(false);
  const [repaymentForm, setRepaymentForm] = useState({
    amount: "", payment_date: new Date().toLocaleDateString("en-CA"),
    payment_method: "mobile_money", reference: "", notes: "",
  });
  const [savingRepayment, setSavingRepayment] = useState(false);

  const loadData = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    setError("");

    try {
      const [productResult, applicationResult, loanResult] =
        await Promise.all([
          platformLendingApi.listProducts(),
          platformLendingApi.listApplications(),
          platformLendingApi.listLoans({ per_page: 100 }),
        ]);

      setProducts(productResult.products || []);
      setApplications(applicationResult.applications || []);
      setLoans(loanResult.loans || []);
    } catch (err) {
      setError(err.message || "PesaPilot Lending could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const pendingApplication = useMemo(
    () => applications.find((item) => item.status === "pending"),
    [applications]
  );

  async function openLoan(loan) {
    setSelectedLoan(loan);
    setLoanDetails(null);
    setDetailsLoading(true);
    setError("");

    try {
      const result = await platformLendingApi.getLoan(loan.id);
      setLoanDetails(result);
    } catch (err) {
      setError(err.message || "Loan details could not be loaded.");
    } finally {
      setDetailsLoading(false);
    }
  }

  function startApplication(product) {
    setSelectedProduct(product);
    setApplicationForm({
      amount: "",
      term_count: String(product.min_term_count),
      purpose: "",
    });
    setError("");
    setNotice("");
    setShowApply(true);
  }

  async function submitApplication(event) {
    event.preventDefault();
    if (!selectedProduct || savingApplication) return;

    setSavingApplication(true);
    setError("");
    setNotice("");

    try {
      const amount = toMinorUnits(
        applicationForm.amount,
        selectedProduct.currency_code
      );

      await platformLendingApi.createApplication({
        product_id: selectedProduct.id,
        requested_principal_minor: amount,
        term_count: Number(applicationForm.term_count),
        purpose: applicationForm.purpose.trim(),
      });

      setShowApply(false);
      setNotice("Your loan application has been submitted for review.");
      setTab("applications");
      await loadData(true);
    } catch (err) {
      setError(err.message || "Your application could not be submitted.");
    } finally {
      setSavingApplication(false);
    }
  }

  async function withdrawApplication(application) {
    if (withdrawing) return;
    setWithdrawing(application.id);
    setError("");
    setNotice("");

    try {
      await platformLendingApi.withdrawApplication(application.id);
      setNotice("Your application has been withdrawn.");
      await loadData(true);
    } catch (err) {
      setError(err.message || "The application could not be withdrawn.");
    } finally {
      setWithdrawing(null);
    }
  }

  async function submitRepayment(event) {
    event.preventDefault();
    if (!selectedLoan || savingRepayment) return;

    setSavingRepayment(true);
    setError("");
    setNotice("");

    try {
      const currency = loanDetails?.loan?.currency_code ||
        selectedLoan.currency_code;

      await platformLendingApi.submitRepayment(selectedLoan.id, {
        amount_minor: toMinorUnits(repaymentForm.amount, currency),
        payment_date: repaymentForm.payment_date,
        payment_method: repaymentForm.payment_method,
        reference: repaymentForm.reference.trim() || null,
        notes: repaymentForm.notes.trim() || null,
      });

      setShowRepayment(false);
      setNotice("Repayment submitted. It will remain pending until verified.");
      setRepaymentForm({
        amount: "",
        payment_date: new Date().toLocaleDateString("en-CA"),
        payment_method: "mobile_money",
        reference: "",
        notes: "",
      });

      const result = await platformLendingApi.getLoan(selectedLoan.id);
      setLoanDetails(result);
      await loadData(true);
    } catch (err) {
      setError(err.message || "The repayment could not be submitted.");
    } finally {
      setSavingRepayment(false);
    }
  }

  const activeCurrency = selectedProduct?.currency_code || "KES";
  const detailLoan = loanDetails?.loan;
  const summary = loanDetails?.summary || {};

  return (
    <div className="lending-page">
      <header className="lending-header">
        <div>
          <div className="lending-eyebrow">
            <Landmark size={15} /> PESAPILOT CREDIT
          </div>
          <h1>PesaPilot Lending</h1>
          <p>Explore loan products, apply for financing and manage repayments.</p>
        </div>
        <button
          type="button"
          className="lending-button lending-button-secondary"
          onClick={() => loadData(true)}
          disabled={refreshing}
        >
          <RefreshCw size={16} className={refreshing ? "lending-spin" : ""} />
          Refresh
        </button>
      </header>

      {error && (
        <div className="lending-alert lending-alert-error" role="alert">
          <AlertCircle size={18} /> {error}
          <button type="button" onClick={() => setError("")} aria-label="Dismiss">
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="lending-alert lending-alert-success" role="status">
          <CheckCircle2 size={18} /> {notice}
          <button type="button" onClick={() => setNotice("")} aria-label="Dismiss">
            <X size={16} />
          </button>
        </div>
      )}

      {selectedLoan ? (
        <section className="lending-detail">
          <button
            type="button"
            className="lending-back"
            onClick={() => {
              setSelectedLoan(null);
              setLoanDetails(null);
            }}
          >
            <ArrowLeft size={17} /> Back to my loans
          </button>

          {detailsLoading ? (
            <div className="lending-panel"><Skeleton /></div>
          ) : detailLoan ? (
            <>
              <div className="lending-detail-heading">
                <div>
                  <span className="lending-eyebrow">LOAN ACCOUNT</span>
                  <h2>{detailLoan.product_name}</h2>
                  <span className="lending-muted">
                    {detailLoan.id}
                  </span>
                </div>
                <Status value={detailLoan.status} />
              </div>

              <div className="lending-summary-grid">
                <div className="lending-summary-card">
                  <span>Outstanding balance</span>
                  <strong>{money(detailLoan.outstanding_principal_minor, detailLoan.currency_code)}</strong>
                </div>
                <div className="lending-summary-card">
                  <span>Amount disbursed</span>
                  <strong>{money(detailLoan.disbursed_principal_minor, detailLoan.currency_code)}</strong>
                </div>
                <div className="lending-summary-card">
                  <span>Principal repaid</span>
                  <strong>{money(summary.confirmed_principal_repaid_minor, detailLoan.currency_code)}</strong>
                </div>
                <div className="lending-summary-card">
                  <span>Interest paid</span>
                  <strong>{money(summary.confirmed_interest_paid_minor, detailLoan.currency_code)}</strong>
                </div>
              </div>

              <div className="lending-detail-actions">
                <div>
                  <h3>Repay this loan</h3>
                  <p>Submit a payment record for verification.</p>
                </div>
                {["active", "overdue"].includes(detailLoan.status) && (
                  <button
                    type="button"
                    className="lending-button lending-button-primary"
                    onClick={() => setShowRepayment(true)}
                  >
                    <Plus size={17} /> Submit repayment
                  </button>
                )}
              </div>

              <div className="lending-panel">
                <h3>Loan information</h3>
                <div className="lending-info-grid">
                  <div><span>Approved principal</span><strong>{money(detailLoan.approved_principal_minor, detailLoan.currency_code)}</strong></div>
                  <div><span>Interest method</span><strong>{statusLabel(detailLoan.interest_method)}</strong></div>
                  <div><span>Interest rate</span><strong>{detailLoan.interest_rate_percent}%</strong></div>
                  <div><span>Repayment frequency</span><strong>{statusLabel(detailLoan.repayment_frequency)}</strong></div>
                  <div><span>Term</span><strong>{detailLoan.term_count} periods</strong></div>
                  <div><span>Due date</span><strong>{dateLabel(detailLoan.due_date)}</strong></div>
                </div>
              </div>

              <div className="lending-panel">
                <h3>Repayment schedule</h3>
                {loanDetails.installments?.length ? (
                  <div className="lending-table-wrap">
                    <table className="lending-table">
                      <thead><tr>
                        <th>#</th><th>Due date</th><th>Amount</th>
                        <th>Paid</th><th>Remaining</th><th>Status</th>
                      </tr></thead>
                      <tbody>
                        {loanDetails.installments.map((item) => (
                          <tr key={item.id}>
                            <td>{item.installment_number}</td>
                            <td>{dateLabel(item.due_date)}</td>
                            <td>{money(item.amount_minor, detailLoan.currency_code)}</td>
                            <td>{money(item.paid_minor, detailLoan.currency_code)}</td>
                            <td>{money(item.remaining_minor, detailLoan.currency_code)}</td>
                            <td><Status value={item.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="lending-muted">No repayment schedule is available yet.</p>}
              </div>

              <div className="lending-panel">
                <h3>Repayment history</h3>
                {loanDetails.payments?.length ? (
                  <div className="lending-table-wrap">
                    <table className="lending-table">
                      <thead><tr>
                        <th>Date</th><th>Amount</th><th>Method</th>
                        <th>Reference</th><th>Status</th>
                      </tr></thead>
                      <tbody>
                        {loanDetails.payments.map((payment) => (
                          <tr key={payment.id}>
                            <td>{dateLabel(payment.payment_date)}</td>
                            <td>{money(payment.amount_minor, detailLoan.currency_code)}</td>
                            <td>{statusLabel(payment.payment_method)}</td>
                            <td>{payment.reference || "—"}</td>
                            <td><Status value={payment.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="lending-muted">No repayments have been submitted.</p>}
              </div>
            </>
          ) : (
            <div className="lending-panel">Loan details are unavailable.</div>
          )}
        </section>
      ) : (
        <>
          <div className="lending-tabs">
            <button className={tab === "products" ? "active" : ""} onClick={() => setTab("products")}>
              <Wallet size={16} /> Loan products
            </button>
            <button className={tab === "applications" ? "active" : ""} onClick={() => setTab("applications")}>
              <FileText size={16} /> My applications
              {applications.length > 0 && <span>{applications.length}</span>}
            </button>
            <button className={tab === "loans" ? "active" : ""} onClick={() => setTab("loans")}>
              <HandCoins size={16} /> My loans
              {loans.length > 0 && <span>{loans.length}</span>}
            </button>
          </div>

          {loading ? (
            <div className="lending-product-grid">
              {[1, 2, 3].map((item) => (
                <div className="lending-panel" key={item}><Skeleton /></div>
              ))}
            </div>
          ) : tab === "products" ? (
            <>
              <div className="lending-section-heading">
                <div>
                  <h2>Available loan products</h2>
                  <p>Compare the terms and choose a product that suits your needs.</p>
                </div>
              </div>
              {pendingApplication && (
                <div className="lending-alert lending-alert-info">
                  <Clock3 size={18} />
                  You already have a pending application. You can submit another after it has been decided or withdrawn.
                </div>
              )}
              {products.length ? (
                <div className="lending-product-grid">
                  {products.map((product) => (
                    <article className="lending-product-card" key={product.id}>
                      <div className="lending-product-icon"><Landmark size={22} /></div>
                      <div className="lending-product-code">{product.code}</div>
                      <h3>{product.name}</h3>
                      <p>{product.description || "A PesaPilot lending product."}</p>
                      <div className="lending-product-amount">
                        <span>Available amount</span>
                        <strong>
                          {money(product.min_principal_minor, product.currency_code)}
                          {" – "}
                          {money(product.max_principal_minor, product.currency_code)}
                        </strong>
                      </div>
                      <div className="lending-product-meta">
                        <div><span>Interest</span><strong>{product.interest_rate_percent}% · {statusLabel(product.interest_method)}</strong></div>
                        <div><span>Repayment</span><strong>{statusLabel(product.repayment_frequency)}</strong></div>
                        <div><span>Term range</span><strong>{product.min_term_count}–{product.max_term_count} periods</strong></div>
                        <div><span>Origination fee</span><strong>{money(product.origination_fee_minor, product.currency_code)}</strong></div>
                      </div>
                      <button
                        type="button"
                        className="lending-button lending-button-primary lending-full-button"
                        disabled={Boolean(pendingApplication)}
                        onClick={() => startApplication(product)}
                      >
                        Apply for this loan <ChevronRight size={17} />
                      </button>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="lending-empty">
                  <Landmark size={34} />
                  <h3>No loan products available</h3>
                  <p>There are currently no active loan products. Please check again later.</p>
                </div>
              )}
            </>
          ) : tab === "applications" ? (
            <>
              <div className="lending-section-heading">
                <div><h2>My applications</h2><p>Track the status of your loan requests.</p></div>
              </div>
              {applications.length ? (
                <div className="lending-record-list">
                  {applications.map((application) => {
                    const product = products.find((item) => item.id === application.product_id);
                    const currency = product?.currency_code || "KES";
                    return (
                      <article className="lending-record-card" key={application.id}>
                        <div className="lending-record-main">
                          <div className="lending-record-icon"><FileText size={20} /></div>
                          <div>
                            <h3>{product?.name || "Loan application"}</h3>
                            <span>Submitted {dateLabel(application.submitted_at)}</span>
                          </div>
                        </div>
                        <div className="lending-record-amount">
                          <strong>{money(application.requested_principal_minor, currency)}</strong>
                          <span>{application.term_count} periods</span>
                        </div>
                        <Status value={application.status} />
                        {application.approved_principal_minor != null && (
                          <div className="lending-approved">
                            Approved: {money(application.approved_principal_minor, currency)}
                          </div>
                        )}
                        {application.decision_notes && (
                          <p className="lending-decision-note">{application.decision_notes}</p>
                        )}
                        {application.status === "pending" && (
                          <button
                            type="button"
                            className="lending-button lending-button-danger"
                            disabled={withdrawing === application.id}
                            onClick={() => withdrawApplication(application)}
                          >
                            {withdrawing === application.id ? "Withdrawing…" : "Withdraw application"}
                          </button>
                        )}
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="lending-empty">
                  <FileText size={34} /><h3>No applications yet</h3>
                  <p>Explore available products and submit your first loan application.</p>
                  <button className="lending-button lending-button-primary" onClick={() => setTab("products")}>
                    Explore products
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="lending-section-heading">
                <div><h2>My loans</h2><p>View your active and previous PesaPilot loans.</p></div>
              </div>
              {loans.length ? (
                <div className="lending-record-list">
                  {loans.map((loan) => (
                    <button
                      type="button"
                      className="lending-loan-row"
                      key={loan.id}
                      onClick={() => openLoan(loan)}
                    >
                      <div className="lending-record-icon"><HandCoins size={21} /></div>
                      <div className="lending-loan-info">
                        <strong>{loan.product_name}</strong>
                        <span>Due {dateLabel(loan.due_date)}</span>
                      </div>
                      <div className="lending-loan-balance">
                        <strong>{money(loan.outstanding_principal_minor, loan.currency_code)}</strong>
                        <span>Outstanding</span>
                      </div>
                      <Status value={loan.status} />
                      <ChevronRight size={18} />
                    </button>
                  ))}
                </div>
              ) : (
                <div className="lending-empty">
                  <HandCoins size={34} /><h3>No loans yet</h3>
                  <p>Once an application is approved and a loan is disbursed, it will appear here.</p>
                  <button className="lending-button lending-button-primary" onClick={() => setTab("products")}>
                    Browse loan products
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {showApply && selectedProduct && (
        <div className="lending-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !savingApplication) setShowApply(false);
        }}>
          <form className="lending-modal" onSubmit={submitApplication}>
            <div className="lending-modal-heading">
              <div><span className="lending-eyebrow">NEW APPLICATION</span><h2>{selectedProduct.name}</h2></div>
              <button type="button" onClick={() => setShowApply(false)} aria-label="Close" disabled={savingApplication}><X /></button>
            </div>
            <p className="lending-muted">
              Request between {money(selectedProduct.min_principal_minor, activeCurrency)}
              {" and "}{money(selectedProduct.max_principal_minor, activeCurrency)}.
            </p>
            <Field label={`Requested amount (${activeCurrency})`}>
              <input
                type="number" min="0" step={digitsFor(activeCurrency) ? "0.01" : "1"}
                required value={applicationForm.amount}
                onChange={(event) => setApplicationForm((old) => ({ ...old, amount: event.target.value }))}
                placeholder="Enter amount"
              />
            </Field>
            <Field label={`Repayment term (${selectedProduct.repayment_frequency})`}>
              <input
                type="number" min={selectedProduct.min_term_count}
                max={selectedProduct.max_term_count} step="1" required
                value={applicationForm.term_count}
                onChange={(event) => setApplicationForm((old) => ({ ...old, term_count: event.target.value }))}
              />
              <small>Choose between {selectedProduct.min_term_count} and {selectedProduct.max_term_count} periods.</small>
            </Field>
            <Field label="Purpose of the loan">
              <textarea
                required maxLength={500} rows={4}
                value={applicationForm.purpose}
                onChange={(event) => setApplicationForm((old) => ({ ...old, purpose: event.target.value }))}
                placeholder="Explain what you intend to use the loan for"
              />
            </Field>
            <div className="lending-modal-actions">
              <button type="button" className="lending-button lending-button-secondary" onClick={() => setShowApply(false)} disabled={savingApplication}>Cancel</button>
              <button type="submit" className="lending-button lending-button-primary" disabled={savingApplication}>
                <Send size={16} /> {savingApplication ? "Submitting…" : "Submit application"}
              </button>
            </div>
          </form>
        </div>
      )}

      {showRepayment && selectedLoan && (
        <div className="lending-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !savingRepayment) setShowRepayment(false);
        }}>
          <form className="lending-modal" onSubmit={submitRepayment}>
            <div className="lending-modal-heading">
              <div><span className="lending-eyebrow">LOAN REPAYMENT</span><h2>Submit a payment</h2></div>
              <button type="button" onClick={() => setShowRepayment(false)} aria-label="Close" disabled={savingRepayment}><X /></button>
            </div>
            <div className="lending-alert lending-alert-info">
              Your submission is a payment report, not an instant balance update. The PesaPilot team must verify it before it is confirmed.
            </div>
            <Field label={`Amount (${detailLoan?.currency_code || selectedLoan.currency_code})`}>
              <input
                type="number" min="0"
                step={digitsFor(detailLoan?.currency_code || selectedLoan.currency_code) ? "0.01" : "1"}
                required value={repaymentForm.amount}
                onChange={(event) => setRepaymentForm((old) => ({ ...old, amount: event.target.value }))}
                placeholder="Enter amount paid"
              />
            </Field>
            <Field label="Payment date">
              <input type="date" required value={repaymentForm.payment_date}
                onChange={(event) => setRepaymentForm((old) => ({ ...old, payment_date: event.target.value }))} />
            </Field>
            <Field label="Payment method">
              <select value={repaymentForm.payment_method}
                onChange={(event) => setRepaymentForm((old) => ({ ...old, payment_method: event.target.value }))}>
                <option value="mobile_money">Mobile money</option>
                <option value="bank">Bank</option>
                <option value="cash">Cash</option>
                <option value="other">Other</option>
              </select>
            </Field>
            <Field label="Transaction reference (optional)">
              <input maxLength={100} value={repaymentForm.reference}
                onChange={(event) => setRepaymentForm((old) => ({ ...old, reference: event.target.value }))}
                placeholder="e.g. M-PESA confirmation code" />
            </Field>
            <Field label="Additional notes (optional)">
              <textarea rows={3} maxLength={5000} value={repaymentForm.notes}
                onChange={(event) => setRepaymentForm((old) => ({ ...old, notes: event.target.value }))}
                placeholder="Any additional payment details" />
            </Field>
            <div className="lending-modal-actions">
              <button type="button" className="lending-button lending-button-secondary" onClick={() => setShowRepayment(false)} disabled={savingRepayment}>Cancel</button>
              <button type="submit" className="lending-button lending-button-primary" disabled={savingRepayment}>
                <Send size={16} /> {savingRepayment ? "Submitting…" : "Submit payment"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
