import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Plus, Eye, X, Wallet, ArrowDownCircle, ArrowUpCircle, RotateCcw } from "lucide-react";
import { platformOwnerApi } from "../api/client";

const ZERO_DECIMAL = new Set([
  "JPY", "UGX", "RWF", "BIF", "DJF", "GNF", "KMF", "XAF", "XOF",
]);

function digits(currency) {
  return ZERO_DECIMAL.has(currency) ? 0 : 2;
}

function money(minor, currency = "KES") {
  const amount = Number(minor || 0) / (10 ** digits(currency));
  return new Intl.NumberFormat("en", {
    style: "currency",
    currency,
    maximumFractionDigits: digits(currency),
  }).format(amount);
}

function toMinorUnits(value, currency) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) return null;

  const places = digits(currency);
  const multiplier = 10 ** places;
  const scaled = amount * multiplier;

  if (!Number.isSafeInteger(Math.round(scaled))) return null;
  if (Math.abs(scaled - Math.round(scaled)) > 0.000001) return null;

  return Math.round(scaled);
}

function transactionDate(value) {
  return new Date(`${value}T12:00:00`).toISOString();
}

function dateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}

function Status({ value }) {
  return (
    <span className={`platform-owner-status platform-owner-status-${value}`}>
      {value || "unknown"}
    </span>
  );
}

function InvestorForm({ onClose, onSubmit, saving }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [notes, setNotes] = useState("");

  async function submit(event) {
    event.preventDefault();
    await onSubmit({
      name: name.trim(),
      email: email.trim() || null,
      phone: phone.trim() || null,
      notes: notes.trim() || null,
    });
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section className="platform-owner-modal" role="dialog" aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}>
        <div className="platform-owner-modal-header">
          <div>
            <h2>Register investor</h2>
            <p>Add an investor to the PesaPilot lending operation.</p>
          </div>
          <button className="platform-owner-icon-button" onClick={onClose} type="button">
            <X size={18} />
          </button>
        </div>

        <form className="platform-owner-form" onSubmit={submit}>
          <label>
            Investor name
            <input value={name} onChange={(e) => setName(e.target.value)}
              maxLength={160} required />
          </label>
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              maxLength={254} />
          </label>
          <label>
            Phone
            <input value={phone} onChange={(e) => setPhone(e.target.value)}
              maxLength={30} />
          </label>
          <label>
            Notes
            <textarea rows="3" value={notes} onChange={(e) => setNotes(e.target.value)}
              maxLength={5000} />
          </label>
          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="platform-owner-button primary" disabled={saving}>
              {saving ? "Registering..." : "Register investor"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function CapitalTransactionModal({ investor, accounts, type, onClose, onSubmit, saving }) {
  const [accountId, setAccountId] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(new Date().toLocaleDateString("en-CA"));
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");

  const account = accounts.find((item) => item.id === accountId);
  const currency = account?.currency_code || "KES";
  const minorAmount = toMinorUnits(amount, currency);

  const existingCapital = investor.capital?.find(
    (item) => item.currency_code === currency
  );
  const available = existingCapital?.available_minor || 0;

  async function submit(event) {
    event.preventDefault();

    if (!account) return;
    if (minorAmount === null) {
      window.alert(`Enter a valid amount with no more than ${digits(currency)} decimal places.`);
      return;
    }

    if (type === "withdrawal" && minorAmount > available) {
      window.alert(`The investor's available capital is ${money(available, currency)}.`);
      return;
    }

    await onSubmit({
      financial_account_id: account.id,
      amount_minor: minorAmount,
      currency_code: currency,
      [type === "contribution" ? "contribution_date" : "withdrawal_date"]:
        transactionDate(date),
      reference: reference.trim() || null,
      notes: notes.trim() || null,
    });
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section className="platform-owner-modal" role="dialog" aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}>
        <div className="platform-owner-modal-header">
          <div>
            <h2>Record {type}</h2>
            <p>{investor.name}</p>
          </div>
          <button className="platform-owner-icon-button" onClick={onClose} type="button">
            <X size={18} />
          </button>
        </div>

        <form className="platform-owner-form" onSubmit={submit}>
          <label>
            Platform financial account
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
              <option value="">Select account</option>
              {accounts.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} — {item.currency_code} ({item.account_type})
                </option>
              ))}
            </select>
          </label>

          {account && (
            <div className="platform-owner-notice">
              <Wallet size={17} />
              <span>
                {type === "withdrawal"
                  ? `Available investor capital: ${money(available, currency)}`
                  : `Transaction currency: ${currency}`}
              </span>
            </div>
          )}

          <label>
            Amount ({currency}, major units)
            <input type="number" min="0" step={digits(currency) === 0 ? "1" : "0.01"}
              value={amount} onChange={(e) => setAmount(e.target.value)} required />
            <small>The amount is converted to minor units before submission.</small>
          </label>

          <label>
            Transaction date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          </label>

          <label>
            Reference
            <input value={reference} onChange={(e) => setReference(e.target.value)}
              maxLength={100} />
          </label>

          <label>
            Notes
            <textarea rows="3" value={notes} onChange={(e) => setNotes(e.target.value)}
              maxLength={5000} />
          </label>

          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="platform-owner-button primary" disabled={saving || !account}>
              {saving ? "Saving..." : `Record ${type}`}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function ReverseModal({ transaction, type, onClose, onSubmit, saving }) {
  const [reference, setReference] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!reference.trim()) return;
    await onSubmit(transaction.id, { reference: reference.trim() });
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section className="platform-owner-modal" role="dialog" aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}>
        <div className="platform-owner-modal-header">
          <div>
            <h2>Reverse {type}</h2>
            <p>{money(transaction.amount_minor, transaction.currency_code)}</p>
          </div>
          <button className="platform-owner-icon-button" onClick={onClose} type="button">
            <X size={18} />
          </button>
        </div>
        <form className="platform-owner-form" onSubmit={submit}>
          <label>
            Reversal reference
            <input value={reference} onChange={(e) => setReference(e.target.value)}
              maxLength={100} required />
            <small>A reference is required to reverse this posted transaction.</small>
          </label>
          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="platform-owner-button primary" disabled={saving || !reference.trim()}>
              {saving ? "Reversing..." : "Confirm reversal"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function InvestorDetail({ investor, detail, onClose, onTransaction, onReverse, busy }) {
  if (!detail) return null;

  const { contributions = [], withdrawals = [], allocations = [], capital = [] } = detail;

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section className="platform-owner-modal" style={{ width: "min(100%, 900px)" }}
        role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
        <div className="platform-owner-modal-header">
          <div>
            <h2>{investor.name}</h2>
            <p>{investor.email || investor.phone || "Investor details"}</p>
          </div>
          <button className="platform-owner-icon-button" onClick={onClose} type="button">
            <X size={18} />
          </button>
        </div>

        <div className="platform-owner-toolbar">
          <button className="platform-owner-button primary"
            onClick={() => onTransaction("contribution")} disabled={busy}>
            <ArrowDownCircle size={16} /> Contribution
          </button>
          <button className="platform-owner-button secondary"
            onClick={() => onTransaction("withdrawal")} disabled={busy}>
            <ArrowUpCircle size={16} /> Withdrawal
          </button>
        </div>

        <h3>Capital position</h3>
        {capital.length === 0 ? (
          <p className="platform-owner-muted">No capital transactions recorded yet.</p>
        ) : (
          <div className="platform-owner-table-wrap">
            <table className="platform-owner-table">
              <thead><tr>
                <th>Currency</th><th>Contributed</th><th>Withdrawn</th>
                <th>Allocated</th><th>Available</th>
              </tr></thead>
              <tbody>
                {capital.map((item) => (
                  <tr key={item.currency_code}>
                    <td>{item.currency_code}</td>
                    <td>{money(item.contributed_minor, item.currency_code)}</td>
                    <td>{money(item.withdrawn_minor, item.currency_code)}</td>
                    <td>{money(item.allocated_minor, item.currency_code)}</td>
                    <td><strong>{money(item.available_minor, item.currency_code)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3>Contributions</h3>
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead><tr>
              <th>Date</th><th>Amount</th><th>Reference</th><th>Status</th><th>Action</th>
            </tr></thead>
            <tbody>
              {contributions.length === 0 ? (
                <tr><td colSpan="5">No contributions recorded.</td></tr>
              ) : contributions.map((item) => (
                <tr key={item.id}>
                  <td>{dateTime(item.contribution_date)}</td>
                  <td>{money(item.amount_minor, item.currency_code)}</td>
                  <td>{item.reference || "—"}</td>
                  <td><Status value={item.status} /></td>
                  <td>
                    {item.status === "posted" && (
                      <button className="platform-owner-button secondary"
                        disabled={busy} onClick={() => onReverse(item, "contribution")}>
                        <RotateCcw size={14} /> Reverse
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3>Withdrawals</h3>
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead><tr>
              <th>Date</th><th>Amount</th><th>Reference</th><th>Status</th><th>Action</th>
            </tr></thead>
            <tbody>
              {withdrawals.length === 0 ? (
                <tr><td colSpan="5">No withdrawals recorded.</td></tr>
              ) : withdrawals.map((item) => (
                <tr key={item.id}>
                  <td>{dateTime(item.withdrawal_date)}</td>
                  <td>{money(item.amount_minor, item.currency_code)}</td>
                  <td>{item.reference || "—"}</td>
                  <td><Status value={item.status} /></td>
                  <td>
                    {item.status === "posted" && (
                      <button className="platform-owner-button secondary"
                        disabled={busy} onClick={() => onReverse(item, "withdrawal")}>
                        <RotateCcw size={14} /> Reverse
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3>Loan allocations</h3>
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead><tr>
              <th>Loan ID</th><th>Disbursement ID</th><th>Allocated</th><th>Outstanding</th>
            </tr></thead>
            <tbody>
              {allocations.length === 0 ? (
                <tr><td colSpan="4">No loan allocations.</td></tr>
              ) : allocations.map((item) => (
                <tr key={item.id}>
                  <td>{item.loan_id}</td>
                  <td>{item.disbursement_id}</td>
                  <td>{money(item.amount_minor, item.currency_code)}</td>
                  <td>{money(item.outstanding_minor, item.currency_code)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default function PlatformOwnerInvestors() {
  const [investors, setInvestors] = useState([]);
  const [filter, setFilter] = useState("true");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [transactionType, setTransactionType] = useState("");
  const [accounts, setAccounts] = useState([]);
  const [reverseTarget, setReverseTarget] = useState(null);

  const loadInvestors = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await platformOwnerApi.listInvestors({
        search: search.trim() || undefined,
        is_active: filter,
        page: 1,
        per_page: 100,
      });
      setInvestors(response.investors || []);
    } catch (err) {
      setError(err.message || "Investors could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    loadInvestors();
  }, [loadInvestors]);

  async function createInvestor(payload) {
    setSaving(true);
    setError("");
    try {
      await platformOwnerApi.createInvestor(payload);
      setShowCreate(false);
      setNotice("Investor registered successfully.");
      await loadInvestors();
    } catch (err) {
      setError(err.message || "Investor could not be registered.");
    } finally {
      setSaving(false);
    }
  }

  async function openInvestor(investor) {
    setSaving(true);
    setError("");
    try {
      const response = await platformOwnerApi.getInvestor(investor.id);
      setSelected(investor);
      setDetail(response);
    } catch (err) {
      setError(err.message || "Investor details could not be loaded.");
    } finally {
      setSaving(false);
    }
  }

  async function openTransaction(type) {
    setError("");
    try {
      const response = await platformOwnerApi.listFinancialAccounts();
      const availableAccounts = response.accounts || [];
      setAccounts(availableAccounts);
      if (!availableAccounts.length) {
        setError("No active platform financial accounts are available.");
        return;
      }
      setTransactionType(type);
    } catch (err) {
      setError(err.message || "Platform financial accounts could not be loaded.");
    }
  }

  async function saveTransaction(payload) {
    setSaving(true);
    setError("");
    try {
      if (transactionType === "contribution") {
        await platformOwnerApi.recordContribution(selected.id, payload);
      } else {
        await platformOwnerApi.recordWithdrawal(selected.id, payload);
      }
      setTransactionType("");
      setNotice(`Investor ${transactionType} recorded successfully.`);
      await refreshSelected();
    } catch (err) {
      setError(err.message || `Investor ${transactionType} could not be recorded.`);
    } finally {
      setSaving(false);
    }
  }

  async function refreshSelected() {
    if (!selected) return;
    const [investorResponse, listResponse] = await Promise.all([
      platformOwnerApi.getInvestor(selected.id),
      platformOwnerApi.listInvestors({
        search: search.trim() || undefined,
        is_active: filter,
        page: 1,
        per_page: 100,
      }),
    ]);
    setDetail(investorResponse);
    setInvestors(listResponse.investors || []);
    setSelected((current) =>
      listResponse.investors?.find((item) => item.id === current?.id) || current
    );
  }

  async function reverseTransaction(transactionId, payload) {
    const { type } = reverseTarget;
    setSaving(true);
    setError("");
    try {
      if (type === "contribution") {
        await platformOwnerApi.reverseContribution(transactionId, payload);
      } else {
        await platformOwnerApi.reverseWithdrawal(transactionId, payload);
      }
      setReverseTarget(null);
      setNotice(`Investor ${type} reversed successfully.`);
      await refreshSelected();
    } catch (err) {
      setError(err.message || `Investor ${type} could not be reversed.`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="platform-owner-page platform-owner-investors-page">
      <div className="platform-owner-page-header">
        <div>
          <span className="platform-owner-eyebrow">LENDING OPERATIONS</span>
          <h1>Investors</h1>
          <p>Manage investor capital, contributions, withdrawals and loan allocations.</p>
        </div>
        <div className="platform-owner-toolbar">
          <button className="platform-owner-button secondary" onClick={loadInvestors}
            disabled={loading}>
            <RefreshCw size={16} className={loading ? "platform-owner-spin" : ""} />
            Refresh
          </button>
          <button className="platform-owner-button primary" onClick={() => setShowCreate(true)}>
            <Plus size={16} /> Register investor
          </button>
        </div>
      </div>

      {error && <div className="platform-owner-notice error">{error}</div>}
      {notice && <div className="platform-owner-notice success">{notice}</div>}

      <div className="platform-owner-toolbar">
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name, email or phone..." />
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="true">Active investors</option>
          <option value="false">Inactive investors</option>
          <option value="">All investors</option>
        </select>
      </div>

      {loading ? (
        <div className="platform-owner-empty">Loading investors...</div>
      ) : investors.length === 0 ? (
        <div className="platform-owner-empty">
          <Wallet size={28} />
          <h3>No investors found</h3>
          <p>Register an investor to begin tracking external capital.</p>
        </div>
      ) : (
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead><tr>
              <th>Investor</th><th>Contact</th><th>Status</th>
              <th>Capital position</th><th>Action</th>
            </tr></thead>
            <tbody>
              {investors.map((investor) => (
                <tr key={investor.id}>
                  <td><strong>{investor.name}</strong></td>
                  <td>{investor.email || investor.phone || "—"}</td>
                  <td><Status value={investor.is_active ? "active" : "inactive"} /></td>
                  <td>
                    {investor.capital?.length ? investor.capital.map((item) => (
                      <div key={item.currency_code}>
                        <strong>{money(item.available_minor, item.currency_code)}</strong>
                        <span className="platform-owner-muted"> available</span>
                      </div>
                    )) : "No capital recorded"}
                  </td>
                  <td>
                    <button className="platform-owner-button secondary"
                      onClick={() => openInvestor(investor)} disabled={saving}>
                      <Eye size={15} /> View details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <InvestorForm onClose={() => setShowCreate(false)}
          onSubmit={createInvestor} saving={saving} />
      )}

      {selected && detail && (
        <InvestorDetail
          investor={selected}
          detail={detail}
          onClose={() => { setSelected(null); setDetail(null); }}
          onTransaction={openTransaction}
          onReverse={(transaction, type) => setReverseTarget({ transaction, type })}
          busy={saving}
        />
      )}

      {selected && transactionType && (
        <CapitalTransactionModal
          investor={selected}
          accounts={accounts}
          type={transactionType}
          onClose={() => setTransactionType("")}
          onSubmit={saveTransaction}
          saving={saving}
        />
      )}

      {reverseTarget && (
        <ReverseModal
          transaction={reverseTarget.transaction}
          type={reverseTarget.type}
          onClose={() => setReverseTarget(null)}
          onSubmit={reverseTransaction}
          saving={saving}
        />
      )}
    </div>
  );
}
