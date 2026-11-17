import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  Search,
  Eye,
  Wallet,
  X,
  Plus,
  AlertCircle,
  CheckCircle2,
  Clock,
  Banknote,
  BriefcaseBusiness,
} from "lucide-react";
import { platformOwnerApi } from "../api/client";

const STATUSES = [
  "all",
  "created",
  "funding",
  "active",
  "paid",
  "overdue",
  "written_off",
  "cancelled",
];

const ZERO_DECIMAL = new Set(["JPY", "KRW", "UGX", "RWF", "BIF", "XAF", "XOF"]);

function minorToMajor(value, currency) {
  return Number(value || 0) / (ZERO_DECIMAL.has(currency) ? 1 : 100);
}

function majorToMinor(value, currency) {
  return Math.round(
    Number(value) * (ZERO_DECIMAL.has(currency) ? 1 : 100)
  );
}

function money(minor, currency = "KES") {
  return new Intl.NumberFormat("en", {
    style: "currency",
    currency,
    maximumFractionDigits: ZERO_DECIMAL.has(currency) ? 0 : 2,
  }).format(minorToMajor(minor, currency));
}

function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString();
}

function dateTimeText(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString();
}

function statusClass(status) {
  return `platform-status platform-status-${status || "unknown"}`;
}

function Status({ value }) {
  return <span className={statusClass(value)}>{String(value || "unknown").replaceAll("_", " ")}</span>;
}

function Metric({ label, value, detail }) {
  return (
    <div className="platform-loan-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function Modal({ title, onClose, children, wide = false }) {
  return (
    <div className="platform-loan-modal-backdrop" onMouseDown={onClose}>
      <section
        className={`platform-loan-modal ${wide ? "platform-loan-modal-wide" : ""}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="platform-loan-modal-header">
          <h2>{title}</h2>
          <button type="button" className="platform-loan-icon-button" onClick={onClose}>
            <X size={19} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export default function PlatformOwnerLoans() {
  const [loans, setLoans] = useState([]);
  const [pagination, setPagination] = useState(null);
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [selectedLoan, setSelectedLoan] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [showDisbursement, setShowDisbursement] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  const [accounts, setAccounts] = useState([]);
  const [investors, setInvestors] = useState([]);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [fundingSource, setFundingSource] = useState("pesapilot_capital");
  const [accountId, setAccountId] = useState("");
  const [amount, setAmount] = useState("");
  const [disbursementDate, setDisbursementDate] = useState(() => {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  });
  const [oneTimeDueDate, setOneTimeDueDate] = useState("");
  const [notes, setNotes] = useState("");
  const [allocations, setAllocations] = useState([
    { investor_id: "", amount: "" },
  ]);
  const [formError, setFormError] = useState("");

  const loadLoans = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    setError("");

    try {
      const result = await platformOwnerApi.listLoans({
        ...(status !== "all" ? { status } : {}),
        page: 1,
        per_page: 100,
      });
      setLoans(result.loans || []);
      setPagination(result.pagination || null);
    } catch (err) {
      setError(err.message || "Could not load the loan portfolio.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [status]);

  useEffect(() => {
    loadLoans();
  }, [loadLoans]);

  const openLoan = async (loanId) => {
    setSelectedLoan(null);
    setDetailError("");
    setDetailLoading(true);

    try {
      const result = await platformOwnerApi.getLoan(loanId);
      setSelectedLoan(result);
    } catch (err) {
      setDetailError(err.message || "Could not load loan details.");
    } finally {
      setDetailLoading(false);
    }
  };

  const openDisbursement = async (loan) => {
    setSelectedLoan((current) => current);
    setShowDisbursement(true);
    setFormError("");
    setNotice("");
    setFundingSource("pesapilot_capital");
    setAccountId("");
    setAmount("");
    setNotes("");
    setOneTimeDueDate("");
    setAllocations([{ investor_id: "", amount: "" }]);
    setAccounts([]);
    setInvestors([]);
    setAccountsLoading(true);

    try {
      const [accountResult, investorResult] = await Promise.all([
        platformOwnerApi.listFinancialAccounts(loan.currency_code),
        platformOwnerApi.listInvestors({ is_active: true, page: 1, per_page: 100 }),
      ]);
      setAccounts(accountResult.accounts || []);
      setInvestors(investorResult.investors || []);
    } catch (err) {
      setFormError(err.message || "Could not load financial accounts and investors.");
    } finally {
      setAccountsLoading(false);
    }
  };

  const activeLoan = selectedLoan?.loan;
  const selectedCurrency = activeLoan?.currency_code || "KES";

  const remainingToDisburse = activeLoan
    ? Math.max(
        0,
        activeLoan.approved_principal_minor - activeLoan.disbursed_principal_minor
      )
    : 0;

  const eligibleInvestors = useMemo(() => {
    return investors
      .filter((investor) => investor.is_active)
      .map((investor) => {
        const capital = (investor.capital || []).find(
          (item) => item.currency_code === selectedCurrency
        );
        return {
          ...investor,
          available_minor: capital?.available_minor || 0,
        };
      })
      .filter((investor) => investor.available_minor > 0);
  }, [investors, selectedCurrency]);

  const allocationTotal = allocations.reduce(
    (total, item) =>
      total + (item.amount ? majorToMinor(item.amount, selectedCurrency) : 0),
    0
  );

  const addAllocation = () => {
    setAllocations((current) => [
      ...current,
      { investor_id: "", amount: "" },
    ]);
  };

  const updateAllocation = (index, field, value) => {
    setAllocations((current) =>
      current.map((item, i) =>
        i === index ? { ...item, [field]: value } : item
      )
    );
  };

  const removeAllocation = (index) => {
    setAllocations((current) =>
      current.length === 1
        ? [{ investor_id: "", amount: "" }]
        : current.filter((_, i) => i !== index)
    );
  };

  const submitDisbursement = async (event) => {
    event.preventDefault();
    setFormError("");

    if (!activeLoan) return;
    if (!accountId) {
      setFormError("Select the platform account used to disburse the funds.");
      return;
    }
    if (!amount || !Number.isFinite(Number(amount)) || Number(amount) <= 0) {
      setFormError("Enter a valid disbursement amount.");
      return;
    }

    const amountMinor = majorToMinor(amount, selectedCurrency);
    if (amountMinor <= 0 || amountMinor > remainingToDisburse) {
      setFormError(
        `The amount cannot exceed the remaining approved amount of ${money(remainingToDisburse, selectedCurrency)}.`
      );
      return;
    }

    let investorAllocations;
    if (fundingSource === "external_investor") {
      const selected = allocations.filter(
        (item) => item.investor_id && item.amount
      );

      if (selected.length === 0) {
        setFormError("Add at least one investor allocation.");
        return;
      }

      if (selected.length !== allocations.length) {
        setFormError("Complete or remove every investor allocation row.");
        return;
      }

      const investorIds = selected.map((item) => item.investor_id);
      if (new Set(investorIds).size !== investorIds.length) {
        setFormError("Each investor can only appear once in this disbursement.");
        return;
      }

      investorAllocations = selected.map((item) => ({
        investor_id: item.investor_id,
        amount_minor: majorToMinor(item.amount, selectedCurrency),
      }));

      if (investorAllocations.some((item) => item.amount_minor <= 0)) {
        setFormError("Investor allocation amounts must be positive.");
        return;
      }

      if (
        investorAllocations.reduce((sum, item) => sum + item.amount_minor, 0) !==
        amountMinor
      ) {
        setFormError("Investor allocations must add up exactly to the disbursement amount.");
        return;
      }

      for (const allocation of investorAllocations) {
        const investor = eligibleInvestors.find(
          (item) => item.id === allocation.investor_id
        );
        if (!investor || allocation.amount_minor > investor.available_minor) {
          setFormError(
            `An allocation exceeds the available capital for ${investor?.name || "the selected investor"}.`
          );
          return;
        }
      }
    }

    if (!disbursementDate) {
      setFormError("Select the disbursement date and time.");
      return;
    }

    if (
      activeLoan.repayment_frequency === "one_time" &&
      oneTimeDueDate &&
      !/^\d{4}-\d{2}-\d{2}$/.test(oneTimeDueDate)
    ) {
      setFormError("Enter a valid one-time repayment due date.");
      return;
    }

    const payload = {
      financial_account_id: accountId,
      amount_minor: amountMinor,
      funding_source: fundingSource,
      disbursement_date: new Date(disbursementDate).toISOString(),
      notes: notes.trim() || undefined,
      ...(investorAllocations ? { investor_allocations: investorAllocations } : {}),
      ...(activeLoan.repayment_frequency === "one_time" && oneTimeDueDate
        ? { one_time_due_date: oneTimeDueDate }
        : {}),
    };

    setSaving(true);
    try {
      await platformOwnerApi.disburseLoan(activeLoan.id, payload);
      setShowDisbursement(false);
      setNotice("Disbursement recorded successfully.");
      await loadLoans(true);
      await openLoan(activeLoan.id);
    } catch (err) {
      setFormError(err.message || "Disbursement could not be recorded.");
    } finally {
      setSaving(false);
    }
  };

  const filteredLoans = loans.filter((loan) => {
    const term = search.trim().toLowerCase();
    if (!term) return true;
    return [
      loan.customer_email,
      loan.product_name,
      loan.id,
      loan.status,
    ].some((value) => String(value || "").toLowerCase().includes(term));
  });

  const detail = selectedLoan;
  const loan = detail?.loan;
  const summary = detail?.summary || {};

  return (
    <div className="platform-owner-page platform-owner-loans-page">
      <div className="platform-owner-page-heading">
        <div>
          <span className="platform-owner-eyebrow">PESAPILOT LENDING BUSINESS</span>
          <h1>Loan Portfolio</h1>
          <p>Manage disbursements and monitor every platform-funded loan.</p>
        </div>
        <button
          type="button"
          className="platform-owner-button platform-owner-button-secondary"
          onClick={() => loadLoans(true)}
          disabled={refreshing}
        >
          <RefreshCw size={16} className={refreshing ? "platform-spin" : ""} />
          Refresh
        </button>
      </div>

      {notice && (
        <div className="platform-loan-notice platform-loan-notice-success">
          <CheckCircle2 size={17} />
          {notice}
          <button type="button" onClick={() => setNotice("")}><X size={15} /></button>
        </div>
      )}

      <div className="platform-loan-toolbar">
        <div className="platform-loan-search">
          <Search size={17} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search customer, product or loan ID"
          />
        </div>
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          {STATUSES.map((item) => (
            <option key={item} value={item}>
              {item === "all" ? "All loan statuses" : item.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="platform-loan-notice platform-loan-notice-error">
          <AlertCircle size={17} /> {error}
        </div>
      )}

      <div className="platform-loan-table-card">
        {loading ? (
          <div className="platform-loan-empty">Loading loan portfolio…</div>
        ) : filteredLoans.length === 0 ? (
          <div className="platform-loan-empty">
            <BriefcaseBusiness size={30} />
            <strong>No loans found</strong>
            <span>Loans matching this filter will appear here.</span>
          </div>
        ) : (
          <div className="platform-loan-table-scroll">
            <table className="platform-loan-table">
              <thead>
                <tr>
                  <th>Customer / Product</th>
                  <th>Approved</th>
                  <th>Disbursed</th>
                  <th>Outstanding</th>
                  <th>Term</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredLoans.map((item) => {
                  const remaining = Math.max(
                    0,
                    item.approved_principal_minor - item.disbursed_principal_minor
                  );
                  return (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.customer_email || "Customer"}</strong>
                        <small>{item.product_name}</small>
                        <small className="platform-loan-id">{item.id}</small>
                      </td>
                      <td>{money(item.approved_principal_minor, item.currency_code)}</td>
                      <td>{money(item.disbursed_principal_minor, item.currency_code)}</td>
                      <td>{money(item.outstanding_principal_minor, item.currency_code)}</td>
                      <td>{item.term_count} {item.repayment_frequency}</td>
                      <td><Status value={item.status} /></td>
                      <td>
                        <div className="platform-loan-row-actions">
                          <button
                            type="button"
                            className="platform-owner-button platform-owner-button-secondary platform-owner-button-small"
                            onClick={() => openLoan(item.id)}
                          >
                            <Eye size={15} /> Details
                          </button>
                          {remaining > 0 && ["created", "funding"].includes(item.status) && (
                            <button
                              type="button"
                              className="platform-owner-button platform-owner-button-primary platform-owner-button-small"
                              onClick={() => {
                                openLoan(item.id).then(() => {});
                                setShowDisbursement(false);
                              }}
                              title="Open loan details before disbursing"
                            >
                              <Wallet size={15} /> Open
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {pagination && (
        <p className="platform-loan-pagination">
          Showing {filteredLoans.length} of {pagination.total} loans
          {pagination.total > 100 ? " (first 100 loaded)" : ""}
        </p>
      )}

      {detailLoading && (
        <Modal title="Loan details" onClose={() => setDetailLoading(false)}>
          <div className="platform-loan-empty">Loading loan details…</div>
        </Modal>
      )}

      {detailError && (
        <Modal title="Loan details" onClose={() => setDetailError("")}>
          <div className="platform-loan-notice platform-loan-notice-error">
            <AlertCircle size={17} /> {detailError}
          </div>
        </Modal>
      )}

      {detail && loan && (
        <Modal
          title="Loan details"
          wide
          onClose={() => setSelectedLoan(null)}
        >
          <div className="platform-loan-detail">
            <div className="platform-loan-detail-heading">
              <div>
                <h3>{loan.product_name}</h3>
                <p>{loan.customer_email} · {loan.id}</p>
              </div>
              <Status value={loan.status} />
            </div>

            <div className="platform-loan-metrics">
              <Metric label="Approved principal" value={money(loan.approved_principal_minor, loan.currency_code)} />
              <Metric label="Total disbursed" value={money(summary.total_disbursed_minor, loan.currency_code)} />
              <Metric label="Outstanding principal" value={money(summary.outstanding_principal_minor, loan.currency_code)} />
              <Metric label="Confirmed repayments" value={money(summary.confirmed_repayments_minor, loan.currency_code)} />
              <Metric label="Interest collected" value={money(summary.confirmed_interest_collected_minor, loan.currency_code)} />
              <Metric label="Pending repayments" value={money(summary.pending_repayments_minor, loan.currency_code)} />
            </div>

            <div className="platform-loan-detail-facts">
              <span><b>Interest:</b> {loan.interest_method} · {loan.interest_rate_percent}%</span>
              <span><b>Frequency:</b> {loan.repayment_frequency}</span>
              <span><b>Term:</b> {loan.term_count}</span>
              <span><b>Start:</b> {dateText(loan.start_date)}</span>
              <span><b>Due:</b> {dateText(loan.due_date)}</span>
            </div>

            {["created", "funding"].includes(loan.status) &&
              loan.disbursed_principal_minor < loan.approved_principal_minor && (
                <div className="platform-loan-disburse-callout">
                  <div>
                    <strong>Ready for disbursement</strong>
                    <span>
                      Remaining approved amount:{" "}
                      {money(
                        loan.approved_principal_minor - loan.disbursed_principal_minor,
                        loan.currency_code
                      )}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="platform-owner-button platform-owner-button-primary"
                    onClick={() => openDisbursement(loan)}
                  >
                    <Banknote size={16} /> Record disbursement
                  </button>
                </div>
              )}

            <section className="platform-loan-detail-section">
              <h3>Disbursements</h3>
              {detail.disbursements?.length ? (
                <div className="platform-loan-table-scroll">
                  <table className="platform-loan-table">
                    <thead><tr><th>Date</th><th>Amount</th><th>Funding source</th><th>Account</th><th>Notes</th></tr></thead>
                    <tbody>
                      {detail.disbursements.map((item) => (
                        <tr key={item.id}>
                          <td>{dateTimeText(item.disbursement_date)}</td>
                          <td>{money(item.amount_minor, item.currency_code)}</td>
                          <td>{item.funding_source.replaceAll("_", " ")}</td>
                          <td>{item.financial_account_id}</td>
                          <td>{item.notes || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="platform-loan-muted">No disbursements recorded.</p>}
            </section>

            <section className="platform-loan-detail-section">
              <h3>Repayment schedule</h3>
              {detail.installments?.length ? (
                <div className="platform-loan-table-scroll">
                  <table className="platform-loan-table">
                    <thead><tr><th>#</th><th>Due date</th><th>Installment</th><th>Principal</th><th>Interest</th><th>Paid</th><th>Remaining</th><th>Status</th></tr></thead>
                    <tbody>
                      {detail.installments.map((item) => (
                        <tr key={item.id}>
                          <td>{item.installment_number}</td>
                          <td>{dateText(item.due_date)}</td>
                          <td>{money(item.amount_minor, loan.currency_code)}</td>
                          <td>{money(item.principal_minor, loan.currency_code)}</td>
                          <td>{money(item.interest_minor, loan.currency_code)}</td>
                          <td>{money(item.paid_minor, loan.currency_code)}</td>
                          <td>{money(item.remaining_minor, loan.currency_code)}</td>
                          <td><Status value={item.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="platform-loan-muted">The repayment schedule is created when the loan is fully disbursed.</p>}
            </section>

            <section className="platform-loan-detail-section">
              <h3>Repayment history</h3>
              {detail.payments?.length ? (
                <div className="platform-loan-table-scroll">
                  <table className="platform-loan-table">
                    <thead><tr><th>Date</th><th>Amount</th><th>Principal</th><th>Interest</th><th>Method</th><th>Reference</th><th>Status</th></tr></thead>
                    <tbody>
                      {detail.payments.map((item) => (
                        <tr key={item.id}>
                          <td>{dateText(item.payment_date)}</td>
                          <td>{money(item.amount_minor, item.currency_code)}</td>
                          <td>{money(item.principal_minor, item.currency_code)}</td>
                          <td>{money(item.interest_minor, item.currency_code)}</td>
                          <td>{item.payment_method}</td>
                          <td>{item.reference || "—"}</td>
                          <td><Status value={item.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="platform-loan-muted">No repayments recorded.</p>}
            </section>
          </div>
        </Modal>
      )}

      {showDisbursement && (
        <Modal
          title="Record loan disbursement"
          onClose={() => !saving && setShowDisbursement(false)}
        >
          <form className="platform-loan-form" onSubmit={submitDisbursement}>
            <p className="platform-loan-form-intro">
              {activeLoan?.product_name} · {activeLoan?.customer_email}
            </p>
            <div className="platform-loan-form-balance">
              <span>Remaining approved amount</span>
              <strong>{money(remainingToDisburse, selectedCurrency)}</strong>
            </div>

            <label>
              Platform financial account
              <select
                value={accountId}
                onChange={(event) => setAccountId(event.target.value)}
                disabled={accountsLoading}
                required
              >
                <option value="">
                  {accountsLoading ? "Loading accounts…" : "Select account"}
                </option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.account_type}
                    {account.institution_name ? ` · ${account.institution_name}` : ""}
                  </option>
                ))}
              </select>
            </label>

            {!accountsLoading && accounts.length === 0 && (
              <div className="platform-loan-notice platform-loan-notice-error">
                No active platform financial account exists for {selectedCurrency}.
              </div>
            )}

            <label>
              Disbursement amount ({selectedCurrency})
              <input
                type="number"
                min="0"
                step={ZERO_DECIMAL.has(selectedCurrency) ? "1" : "0.01"}
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="Enter amount"
                required
              />
            </label>

            <label>
              Funding source
              <select
                value={fundingSource}
                onChange={(event) => setFundingSource(event.target.value)}
              >
                <option value="pesapilot_capital">PesaPilot capital</option>
                <option value="external_investor">External investor capital</option>
              </select>
            </label>

            {fundingSource === "external_investor" && (
              <div className="platform-loan-allocation-box">
                <div className="platform-loan-allocation-heading">
                  <strong>Investor allocations</strong>
                  <button type="button" onClick={addAllocation}>
                    <Plus size={15} /> Add investor
                  </button>
                </div>
                {allocations.map((item, index) => (
                  <div className="platform-loan-allocation-row" key={index}>
                    <select
                      value={item.investor_id}
                      onChange={(event) =>
                        updateAllocation(index, "investor_id", event.target.value)
                      }
                      required
                    >
                      <option value="">Select investor</option>
                      {eligibleInvestors.map((investor) => (
                        <option
                          key={investor.id}
                          value={investor.id}
                          disabled={allocations.some(
                            (row, rowIndex) =>
                              rowIndex !== index && row.investor_id === investor.id
                          )}
                        >
                          {investor.name} · available{" "}
                          {money(investor.available_minor, selectedCurrency)}
                        </option>
                      ))}
                    </select>
                    <input
                      type="number"
                      min="0"
                      step={ZERO_DECIMAL.has(selectedCurrency) ? "1" : "0.01"}
                      value={item.amount}
                      onChange={(event) =>
                        updateAllocation(index, "amount", event.target.value)
                      }
                      placeholder="Amount"
                      required
                    />
                    <button
                      type="button"
                      className="platform-owner-button platform-owner-button-secondary platform-owner-button-small"
                      onClick={() => removeAllocation(index)}
                      aria-label="Remove investor"
                    >
                      <X size={15} />
                    </button>
                  </div>
                ))}
                <div className="platform-loan-allocation-total">
                  <span>Allocated</span>
                  <strong>{money(allocationTotal, selectedCurrency)}</strong>
                  <span>of {amount ? money(majorToMinor(amount, selectedCurrency), selectedCurrency) : money(0, selectedCurrency)}</span>
                </div>
                {eligibleInvestors.length === 0 && (
                  <p className="platform-loan-muted">
                    No investors currently have available {selectedCurrency} capital.
                  </p>
                )}
              </div>
            )}

            <label>
              Disbursement date and time
              <input
                type="datetime-local"
                value={disbursementDate}
                onChange={(event) => setDisbursementDate(event.target.value)}
                required
              />
            </label>

            {activeLoan?.repayment_frequency === "one_time" && (
              <label>
                One-time repayment due date
                <input
                  type="date"
                  value={oneTimeDueDate}
                  onChange={(event) => setOneTimeDueDate(event.target.value)}
                />
              </label>
            )}

            <label>
              Notes (optional)
              <textarea
                rows="3"
                maxLength="5000"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Disbursement notes"
              />
            </label>

            {formError && (
              <div className="platform-loan-notice platform-loan-notice-error">
                <AlertCircle size={16} /> {formError}
              </div>
            )}

            <div className="platform-loan-form-actions">
              <button
                type="button"
                className="platform-owner-button platform-owner-button-secondary"
                onClick={() => setShowDisbursement(false)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="platform-owner-button platform-owner-button-primary"
                disabled={saving || accountsLoading || accounts.length === 0}
              >
                {saving ? "Recording…" : "Record disbursement"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
