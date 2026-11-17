import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  CheckCircle,
  XCircle,
  FileText,
  X,
  AlertCircle,
  Plus,
} from "lucide-react";
import { platformOwnerApi } from "../api/client";

const FILTERS = [
  { label: "All applications", value: "" },
  { label: "Pending", value: "pending" },
  { label: "Approved", value: "approved" },
  { label: "Rejected", value: "rejected" },
  { label: "Withdrawn", value: "withdrawn" },
];

const ZERO_DECIMAL = new Set([
  "JPY", "UGX", "RWF", "BIF", "DJF", "GNF", "KMF", "XAF", "XOF",
]);

function money(minor, currency = "KES") {
  const divisor = ZERO_DECIMAL.has(currency) ? 1 : 100;
  const amount = Number(minor || 0) / divisor;

  return new Intl.NumberFormat("en", {
    style: "currency",
    currency,
    maximumFractionDigits: ZERO_DECIMAL.has(currency) ? 0 : 2,
  }).format(amount);
}

function dateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function statusClass(status) {
  return `platform-owner-status platform-owner-status-${status}`;
}

function DecisionModal({ application, onClose, onSubmit, saving }) {
  const [decision, setDecision] = useState("approved");
  const [approvedAmount, setApprovedAmount] = useState(
    String(application.requested_principal_minor)
  );
  const [notes, setNotes] = useState("");

  async function submit(event) {
    event.preventDefault();

    const payload = {
      decision,
      decision_notes: notes.trim() || null,
    };

    if (decision === "approved") {
      const amount = Number(approvedAmount);
      if (!Number.isSafeInteger(amount) || amount <= 0) {
        window.alert("Enter a valid approved principal amount in minor units.");
        return;
      }
      if (amount > application.requested_principal_minor) {
        window.alert("Approved amount cannot exceed the requested amount.");
        return;
      }
      payload.approved_principal_minor = amount;
    }

    await onSubmit(application.id, payload);
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section
        className="platform-owner-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="application-decision-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="platform-owner-modal-header">
          <div>
            <h2 id="application-decision-title">Review application</h2>
            <p>{application.customer_email}</p>
          </div>
          <button type="button" className="platform-owner-icon-button" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <div className="platform-owner-review-summary">
          <div>
            <span>Loan product</span>
            <strong>{application.product_name}</strong>
          </div>
          <div>
            <span>Requested amount</span>
            <strong>
              {money(application.requested_principal_minor, application.currency_code)}
            </strong>
          </div>
          <div>
            <span>Requested term</span>
            <strong>{application.term_count} periods</strong>
          </div>
          <div>
            <span>Submitted</span>
            <strong>{dateTime(application.submitted_at)}</strong>
          </div>
        </div>

        {application.purpose && (
          <div className="platform-owner-purpose">
            <span>Purpose</span>
            <p>{application.purpose}</p>
          </div>
        )}

        <form onSubmit={submit} className="platform-owner-form">
          <label>
            Decision
            <select value={decision} onChange={(event) => setDecision(event.target.value)}>
              <option value="approved">Approve application</option>
              <option value="rejected">Reject application</option>
            </select>
          </label>

          {decision === "approved" && (
            <label>
              Approved principal (minor currency units)
              <input
                type="number"
                min="1"
                max={application.requested_principal_minor}
                step="1"
                required
                value={approvedAmount}
                onChange={(event) => setApprovedAmount(event.target.value)}
              />
              <small>
                Maximum: {money(application.requested_principal_minor, application.currency_code)}
              </small>
            </label>
          )}

          <label>
            Decision notes
            <textarea
              rows="3"
              maxLength="5000"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Optional notes for the decision..."
            />
          </label>

          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="platform-owner-button primary" disabled={saving}>
              {saving ? "Saving..." : "Save decision"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function PlatformOwnerApplications() {
  const [applications, setApplications] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState(null);
  const [creatingLoan, setCreatingLoan] = useState("");

  const loadApplications = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const response = await platformOwnerApi.listApplications({
        status: filter || undefined,
        page: 1,
        per_page: 100,
      });
      setApplications(response.applications || []);
    } catch (err) {
      setError(err.message || "Applications could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  async function saveDecision(applicationId, payload) {
    setSaving(true);
    setError("");
    setNotice("");

    try {
      await platformOwnerApi.decideApplication(applicationId, payload);
      setSelected(null);
      setNotice(`Application ${payload.decision} successfully.`);
      await loadApplications();
    } catch (err) {
      setError(err.message || "The application decision could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function createLoan(application) {
    const confirmed = window.confirm(
      `Create a platform loan for ${application.customer_email} using the approved application?`
    );
    if (!confirmed) return;

    setCreatingLoan(application.id);
    setError("");
    setNotice("");

    try {
      const response = await platformOwnerApi.createLoanFromApplication(application.id);
      setNotice(`Loan created successfully. Loan ID: ${response.loan?.id || "Created"}`);
      await loadApplications();
    } catch (err) {
      setError(err.message || "The loan could not be created.");
    } finally {
      setCreatingLoan("");
    }
  }

  const pendingCount = applications.filter((item) => item.status === "pending").length;

  return (
    <div className="platform-owner-page">
      <div className="platform-owner-page-header">
        <div>
          <span className="platform-owner-eyebrow">LENDING OPERATIONS</span>
          <h1>Loan applications</h1>
          <p>Review customer requests, record decisions and create approved loans.</p>
        </div>
        <button
          type="button"
          className="platform-owner-button secondary"
          onClick={loadApplications}
          disabled={loading}
        >
          <RefreshCw size={16} className={loading ? "platform-owner-spin" : ""} />
          Refresh
        </button>
      </div>

      {notice && (
        <div className="platform-owner-notice success">
          <CheckCircle size={17} />
          {notice}
          <button type="button" onClick={() => setNotice("")} aria-label="Dismiss">
            <X size={16} />
          </button>
        </div>
      )}

      {error && (
        <div className="platform-owner-notice error">
          <AlertCircle size={17} />
          {error}
          <button type="button" onClick={() => setError("")} aria-label="Dismiss">
            <X size={16} />
          </button>
        </div>
      )}

      <div className="platform-owner-stat-row">
        <div className="platform-owner-stat-card">
          <span>Applications loaded</span>
          <strong>{applications.length}</strong>
        </div>
        <div className="platform-owner-stat-card">
          <span>Pending in current view</span>
          <strong>{pendingCount}</strong>
        </div>
      </div>

      <div className="platform-owner-toolbar">
        <div className="platform-owner-filter-tabs">
          {FILTERS.map((item) => (
            <button
              type="button"
              key={item.value || "all"}
              className={filter === item.value ? "active" : ""}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="platform-owner-empty">Loading applications...</div>
      ) : applications.length === 0 ? (
        <div className="platform-owner-empty">
          <FileText size={32} />
          <h3>No applications found</h3>
          <p>There are no applications matching this filter.</p>
        </div>
      ) : (
        <div className="platform-owner-table-wrap">
          <table className="platform-owner-table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Product</th>
                <th>Requested</th>
                <th>Approved</th>
                <th>Term</th>
                <th>Submitted</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {applications.map((application) => (
                <tr key={application.id}>
                  <td>
                    <strong>{application.customer_email || "Customer"}</strong>
                    <small>{application.customer_id}</small>
                  </td>
                  <td>
                    <strong>{application.product_name}</strong>
                    <small>{application.currency_code}</small>
                  </td>
                  <td>{money(application.requested_principal_minor, application.currency_code)}</td>
                  <td>
                    {application.approved_principal_minor == null
                      ? "—"
                      : money(application.approved_principal_minor, application.currency_code)}
                  </td>
                  <td>{application.term_count}</td>
                  <td>{dateTime(application.submitted_at)}</td>
                  <td>
                    <span className={statusClass(application.status)}>
                      {application.status}
                    </span>
                  </td>
                  <td>
                    <div className="platform-owner-row-actions">
                      {application.status === "pending" && (
                        <button
                          type="button"
                          className="platform-owner-button small primary"
                          onClick={() => setSelected(application)}
                        >
                          Review
                        </button>
                      )}
                      {application.status === "approved" && (
                        <button
                          type="button"
                          className="platform-owner-button small primary"
                          onClick={() => createLoan(application)}
                          disabled={creatingLoan === application.id}
                        >
                          <Plus size={14} />
                          {creatingLoan === application.id ? "Creating..." : "Create loan"}
                        </button>
                      )}
                      {application.status !== "pending" && application.status !== "approved" && (
                        <span className="platform-owner-muted">No action</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <DecisionModal
          application={selected}
          onClose={() => setSelected(null)}
          onSubmit={saveDecision}
          saving={saving}
        />
      )}
    </div>
  );
}
