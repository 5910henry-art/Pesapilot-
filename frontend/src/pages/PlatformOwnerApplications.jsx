
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  CheckCircle,
  XCircle,
  FileText,
  X,
  AlertCircle,
  Plus,
  Search,
  Clock3,
  Wallet,
  Eye,
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
  const amount = Number(minor || 0) /
    (ZERO_DECIMAL.has(currency) ? 1 : 100);

  try {
    return new Intl.NumberFormat("en", {
      style: "currency",
      currency,
      maximumFractionDigits: ZERO_DECIMAL.has(currency) ? 0 : 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString()}`;
  }
}

function dateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString("en-KE", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
}

function statusClass(status) {
  return `platform-owner-status platform-owner-status-${status}`;
}

function DecisionModal({ application, onClose, onSubmit, saving, error }) {
  const [decision, setDecision] = useState("approved");
  const [approvedAmount, setApprovedAmount] = useState(
    String(application.requested_principal_minor)
  );
  const [notes, setNotes] = useState("");
  const [customerMessage, setCustomerMessage] = useState("");
  const [amountError, setAmountError] = useState("");

  const applicant = application.applicant || null;
  const assessment = application.affordability_assessment || null;

  async function submit(event) {
    event.preventDefault();

    const payload = {
      decision,
      decision_notes: notes.trim() || null,
      customer_decision_message: customerMessage.trim() || null,
    };

    if (decision === "approved") {
      const amount = Number(approvedAmount);

      if (
        !Number.isSafeInteger(amount) ||
        amount <= 0 ||
        amount > application.requested_principal_minor
      ) {
        setAmountError("Enter a valid amount not exceeding the requested amount.");
        return;
      }

      payload.approved_principal_minor = amount;
    }

    await onSubmit(application.id, payload);
  }

  return (
    <div
      className="platform-owner-modal-backdrop"
      onMouseDown={onClose}
    >
      <section
        className="platform-owner-modal loan-review-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="application-decision-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="platform-owner-modal-header">
          <div>
            <span className="platform-owner-eyebrow">APPLICATION REVIEW</span>
            <h2 id="application-decision-title">Review application</h2>
            <p>{application.customer_email || "Customer"}</p>
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
            <span>Loan product</span>
            <strong>{application.product_name || "—"}</strong>
          </div>
          <div>
            <span>Requested amount</span>
            <strong>
              {money(
                application.requested_principal_minor,
                application.currency_code
              )}
            </strong>
          </div>
          <div>
            <span>Repayment term</span>
            <strong>{application.term_count} periods</strong>
          </div>
          <div>
            <span>Application date</span>
            <strong>{dateTime(application.submitted_at)}</strong>
          </div>
        </div>

        {applicant && (
          <>
            <div className="platform-owner-purpose">
              <span>Applicant information</span>
              <div className="platform-owner-review-summary">
                <div>
                  <span>Full name</span>
                  <strong>{applicant.full_name || "—"}</strong>
                </div>
                <div>
                  <span>Phone</span>
                  <strong>{applicant.phone || "—"}</strong>
                </div>
                <div>
                  <span>Email</span>
                  <strong>{applicant.email || "—"}</strong>
                </div>
                <div>
                  <span>Residential address</span>
                  <strong>{applicant.residential_address || "—"}</strong>
                </div>
                <div>
                  <span>Employment status</span>
                  <strong>
                    {applicant.employment_status
                      ? applicant.employment_status.replaceAll("_", " ")
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>Employer / business</span>
                  <strong>{applicant.employer_business_name || "—"}</strong>
                </div>
              </div>
            </div>

            <div className="platform-owner-purpose">
              <span>Declared monthly finances</span>
              <div className="platform-owner-review-summary">
                <div>
                  <span>Monthly income</span>
                  <strong>
                    {money(applicant.monthly_income_minor, application.currency_code)}
                  </strong>
                </div>
                <div>
                  <span>Monthly expenses</span>
                  <strong>
                    {money(applicant.monthly_expenses_minor, application.currency_code)}
                  </strong>
                </div>
                <div>
                  <span>Existing monthly repayments</span>
                  <strong>
                    {money(
                      applicant.existing_monthly_repayments_minor,
                      application.currency_code
                    )}
                  </strong>
                </div>
              </div>
            </div>

            {(applicant.explanation ||
              applicant.alternative_contact ||
              applicant.additional_notes) && (
              <div className="platform-owner-purpose">
                <span>Additional applicant information</span>

                {applicant.explanation && (
                  <p>
                    <strong>Application explanation:</strong>{" "}
                    {applicant.explanation}
                  </p>
                )}

                {applicant.alternative_contact && (
                  <p>
                    <strong>Alternative contact:</strong>{" "}
                    {applicant.alternative_contact.name || "—"} ·{" "}
                    {applicant.alternative_contact.phone || "—"} ·{" "}
                    {applicant.alternative_contact.relationship || "—"}
                  </p>
                )}

                {applicant.additional_notes && (
                  <p>
                    <strong>Additional notes:</strong>{" "}
                    {applicant.additional_notes}
                  </p>
                )}
              </div>
            )}
          </>
        )}

        {assessment && (
          <div className="platform-owner-purpose">
            <span>Affordability assessment · Version {assessment.version}</span>

            <div className="platform-owner-review-summary">
              <div>
                <span>Assessment status</span>
                <strong>{assessment.status.replaceAll("_", " ")}</strong>
              </div>
              <div>
                <span>Recommended monthly instalment</span>
                <strong>
                  {money(
                    assessment.recommended_monthly_instalment_minor,
                    application.currency_code
                  )}
                </strong>
              </div>
              <div>
                <span>Estimated affordable principal</span>
                <strong>
                  {money(
                    assessment.estimated_affordable_principal_minor,
                    application.currency_code
                  )}
                </strong>
              </div>
              <div>
                <span>Evidence period</span>
                <strong>
                  {assessment.evidence_window_months || "—"} months
                </strong>
              </div>
              <div>
                <span>Customer-authorized workspace records</span>
                <strong>
                  {assessment.source_workspace_id
                    ? "Included"
                    : "Not included"}
                </strong>
              </div>
            </div>

            <p>
              {assessment.status === "verified"
                ? "Available authorized workspace records support the declared figures. This does not constitute external identity or bank verification."
                : assessment.status === "needs_review"
                  ? "The available evidence contains discrepancies that require human review."
                  : assessment.status === "high_risk"
                    ? "The assessment identified elevated repayment risk. Review the evidence carefully before deciding."
                    : "There are insufficient records to substantiate the declared figures. This is not an automatic rejection."}
            </p>

            <p>
              This assessment is advisory. The platform owner makes the final
              lending decision.
            </p>
          </div>
        )}

        {application.purpose && (
          <div className="platform-owner-purpose">
            <span>Loan purpose</span>
            <p>{application.purpose}</p>
          </div>
        )}

        <form onSubmit={submit} className="platform-owner-form">
          {error && (
            <div className="platform-owner-notice error" role="alert">
              <AlertCircle size={17} />
              <span>{error}</span>
            </div>
          )}
          <label>
            Application decision
            <select
              value={decision}
              onChange={(event) => setDecision(event.target.value)}
            >
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
                onChange={(event) => {
                  setApprovedAmount(event.target.value);
                  setAmountError("");
                }}
                aria-invalid={Boolean(amountError)}
                aria-describedby={amountError ? "approved-amount-error" : undefined}
              />
              {amountError && (
                <span
                  id="approved-amount-error"
                  className="platform-owner-notice error"
                  role="alert"
                >
                  <AlertCircle size={15} />
                  {amountError}
                </span>
              )}
              <small>
                Maximum:{" "}
                {money(
                  application.requested_principal_minor,
                  application.currency_code
                )}
              </small>
            </label>
          )}

          <label>
            Internal reviewer notes (owner only)
            <textarea
              rows="4"
              maxLength="2000"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Enter private review comments..."
            />
          </label>

          <label>
            Message to customer (visible to applicant)
            <textarea
              rows="3"
              maxLength="1000"
              value={customerMessage}
              onChange={(event) => setCustomerMessage(event.target.value)}
              placeholder="Explain the decision to the applicant..."
            />
          </label>

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
              disabled={saving}
            >
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
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [decisionError, setDecisionError] = useState("");
  const [applicationMessages, setApplicationMessages] = useState({});
  const [selected, setSelected] = useState(null);
  const [allocatingLoan, setAllocatingLoan] = useState("");

  const loadApplications = useCallback(async () => {
    setLoading(true);
    setLoadError("");

    try {
      const response = await platformOwnerApi.listApplications({
        page: 1,
        per_page: 100,
      });

      setApplications(response.applications || []);
    } catch (err) {
      setLoadError(err.message || "Applications could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  const counts = useMemo(() => ({
    total: applications.length,
    pending: applications.filter((a) => a.status === "pending").length,
    approved: applications.filter((a) => a.status === "approved").length,
    rejected: applications.filter((a) => a.status === "rejected").length,
  }), [applications]);

  const filteredApplications = useMemo(() => {
    const query = search.trim().toLowerCase();

    return applications.filter((application) => {
      const matchesStatus = !filter || application.status === filter;

      const searchable = [
        application.customer_email,
        application.customer_id,
        application.product_name,
        application.id,
      ].join(" ").toLowerCase();

      return matchesStatus && (!query || searchable.includes(query));
    });
  }, [applications, filter, search]);

  async function saveDecision(applicationId, payload) {
    setSaving(true);
    setDecisionError("");
    setApplicationMessages((old) => ({
      ...old,
      [applicationId]: null,
    }));

    try {
      await platformOwnerApi.decideApplication(applicationId, payload);
      setSelected(null);
      setApplicationMessages((old) => ({
        ...old,
        [applicationId]: {
          type: "success",
          message: `Application ${payload.decision} successfully.`,
        },
      }));
      await loadApplications();
    } catch (err) {
      setDecisionError(
        err.message || "The application decision could not be saved."
      );
    } finally {
      setSaving(false);
    }
  }

  async function allocateLoan(application) {
    const confirmed = window.confirm(
      `Allocate a platform loan to ${application.customer_email}?`
    );

    if (!confirmed) return;

    setAllocatingLoan(application.id);
    setApplicationMessages((old) => ({
      ...old,
      [application.id]: null,
    }));

    try {
      const response =
        await platformOwnerApi.createLoanFromApplication(application.id);

      setApplicationMessages((old) => ({
        ...old,
        [application.id]: {
          type: "success",
          message: `Loan allocated successfully. Loan ID: ${response.loan?.id || "Allocated"}`,
        },
      }));

      await loadApplications();
    } catch (err) {
      setApplicationMessages((old) => ({
        ...old,
        [application.id]: {
          type: "error",
          message: err.message || "The loan could not be created.",
        },
      }));
    } finally {
      setAllocatingLoan("");
    }
  }

  return (
    <div className="platform-owner-page loan-applications-page">

      <header className="platform-owner-page-header">
        <div>
          <span className="platform-owner-eyebrow">
            PESAPILOT / LENDING OPERATIONS
          </span>
          <h1>Loan applications</h1>
          <p>
            Manage customer applications, assess requests and authorize loans.
          </p>
        </div>

        <button
          type="button"
          className="platform-owner-button secondary"
          onClick={loadApplications}
          disabled={loading}
        >
          <RefreshCw
            size={16}
            className={loading ? "platform-owner-spin" : ""}
          />
          Refresh data
        </button>
      </header>

      {loadError && (
        <div className="platform-owner-notice error" role="alert">
          <AlertCircle size={17} />
          <span>{loadError}</span>
          <button
            type="button"
            onClick={() => setLoadError("")}
            aria-label="Dismiss"
          >
            <X size={16} />
          </button>
        </div>
      )}

      <section className="loan-applications-stats">

        <div className="loan-stat-card">
          <div className="loan-stat-top">
            <span>Total applications</span>
            <div className="loan-stat-icon green">
              <FileText size={19} />
            </div>
          </div>
          <strong>{loading ? "—" : counts.total}</strong>
          <small>Applications loaded</small>
        </div>

        <div className="loan-stat-card">
          <div className="loan-stat-top">
            <span>Pending review</span>
            <div className="loan-stat-icon amber">
              <Clock3 size={19} />
            </div>
          </div>
          <strong>{loading ? "—" : counts.pending}</strong>
          <small>Awaiting a decision</small>
        </div>

        <div className="loan-stat-card">
          <div className="loan-stat-top">
            <span>Approved</span>
            <div className="loan-stat-icon blue">
              <CheckCircle size={19} />
            </div>
          </div>
          <strong>{loading ? "—" : counts.approved}</strong>
          <small>Approved applications</small>
        </div>

        <div className="loan-stat-card">
          <div className="loan-stat-top">
            <span>Rejected</span>
            <div className="loan-stat-icon red">
              <XCircle size={19} />
            </div>
          </div>
          <strong>{loading ? "—" : counts.rejected}</strong>
          <small>Unsuccessful applications</small>
        </div>

      </section>

      <section className="loan-applications-workspace">

        <div className="loan-applications-section-heading">
          <div>
            <h2>Applications overview</h2>
            <p>Review and manage submitted loan requests.</p>
          </div>

          <span className="loan-results-count">
            {filteredApplications.length} results
          </span>
        </div>

        <div className="loan-applications-toolbar">

          <div className="loan-application-search">
            <Search size={18} />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search customer, product or ID..."
              aria-label="Search applications"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
              >
                <X size={15} />
              </button>
            )}
          </div>

          <div className="platform-owner-filter-tabs">
            {FILTERS.map((item) => (
              <button
                type="button"
                key={item.value || "all"}
                className={filter === item.value ? "active" : ""}
                onClick={() => setFilter(item.value)}
              >
                {item.label}
                <span>
                  {item.value === ""
                    ? counts.total
                    : applications.filter((a) => a.status === item.value).length}
                </span>
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="platform-owner-empty">
            <RefreshCw size={23} className="platform-owner-spin" />
            <h3>Loading applications</h3>
            <p>Retrieving loan application records...</p>
          </div>
        ) : filteredApplications.length === 0 ? (
          <div className="platform-owner-empty">
            <FileText size={34} />
            <h3>No applications found</h3>
            <p>
              {search
                ? "Try another search term or change the selected filter."
                : "There are no applications matching this filter."}
            </p>
            {(search || filter) && (
              <button
                type="button"
                className="platform-owner-button secondary"
                onClick={() => {
                  setSearch("");
                  setFilter("");
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <div className="platform-owner-table-wrap loan-applications-table-wrap">
            <table className="platform-owner-table loan-applications-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Loan product</th>
                  <th>Requested amount</th>
                  <th>Approved amount</th>
                  <th>Term</th>
                  <th>Submitted</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>

              <tbody>
                {filteredApplications.map((application) => (
                  <tr key={application.id}>
                    <td>
                      <div className="loan-customer-cell">
                        <div className="loan-customer-avatar">
                          {(application.customer_email || "C")
                            .charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <strong>
                            {application.customer_email || "Customer"}
                          </strong>
                          <small>ID: {application.customer_id || "—"}</small>
                        </div>
                      </div>
                    </td>

                    <td>
                      <strong>{application.product_name || "—"}</strong>
                      <small>{application.currency_code || "KES"}</small>
                    </td>

                    <td className="loan-amount-cell">
                      {money(
                        application.requested_principal_minor,
                        application.currency_code
                      )}
                    </td>

                    <td className="loan-amount-cell">
                      {application.approved_principal_minor == null
                        ? "—"
                        : money(
                            application.approved_principal_minor,
                            application.currency_code
                          )}
                    </td>

                    <td>{application.term_count ?? "—"} periods</td>
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
                            <Eye size={14} />
                            Review
                          </button>
                        )}

                        {application.status === "approved" &&
                          !application.loan_allocated && (
                            <button
                              type="button"
                              className="platform-owner-button small primary"
                              onClick={() => allocateLoan(application)}
                              disabled={allocatingLoan === application.id}
                            >
                              <Plus size={14} />
                              {allocatingLoan === application.id
                                ? "Allocating..."
                                : "Allocate"}
                            </button>
                          )}

                        {application.status === "approved" &&
                          application.loan_allocated && (
                            <span className="platform-owner-muted">
                              Allocated
                            </span>
                          )}

                        {application.status !== "pending" &&
                          application.status !== "approved" && (
                            <span className="platform-owner-muted">
                              No action
                            </span>
                          )}
                        {applicationMessages[application.id] && (
                          <div
                            className={`platform-owner-notice ${
                              applicationMessages[application.id].type === "error"
                                ? "error"
                                : "success"
                            }`}
                            role={
                              applicationMessages[application.id].type === "error"
                                ? "alert"
                                : "status"
                            }
                          >
                            <span>{applicationMessages[application.id].message}</span>
                            <button
                              type="button"
                              onClick={() => setApplicationMessages((old) => ({
                                ...old,
                                [application.id]: null,
                              }))}
                              aria-label="Dismiss"
                            >
                              <X size={16} />
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="loan-applications-footer">
          <span>
            Showing {filteredApplications.length} of {applications.length} loaded applications
          </span>
          <button
            type="button"
            onClick={loadApplications}
            disabled={loading}
          >
            <RefreshCw size={14} />
            Update records
          </button>
        </div>

      </section>

      {selected && (
        <DecisionModal
          application={selected}
          onClose={() => {
            if (!saving) {
              setSelected(null);
              setDecisionError("");
            }
          }}
          onSubmit={saveDecision}
          saving={saving}
          error={decisionError}
        />
      )}

    </div>
  );
}
