import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import { platformOwnerApi } from "../api/client";

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function VerificationStatus({ verified }) {
  return (
    <span className={`platform-owner-status ${verified ? "platform-owner-status-active" : "platform-owner-status-pending"}`}>
      {verified ? "Verified" : "Unverified"}
    </span>
  );
}

function VerificationModal({ user, contactType, onClose, onSubmit, saving, error }) {
  const [evidenceNote, setEvidenceNote] = useState("");
  const contactValue = contactType === "email" ? user.email : user.phone;

  async function submit(event) {
    event.preventDefault();
    if (evidenceNote.trim().length < 10) return;
    await onSubmit(evidenceNote.trim());
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={onClose}>
      <section className="platform-owner-modal" role="dialog" aria-modal="true"
        aria-labelledby="contact-verification-title"
        onMouseDown={(event) => event.stopPropagation()}>
        <div className="platform-owner-modal-header">
          <div>
            <h2 id="contact-verification-title">Verify {contactType}</h2>
            <p>{contactValue}</p>
          </div>
          <button type="button" className="platform-owner-icon-button"
            onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <form className="platform-owner-form" onSubmit={submit}>
          <div className="platform-owner-notice">
            <ShieldCheck size={17} />
            <span>Confirm the user's control of this contact independently before marking it verified.</span>
          </div>
          <label>
            Verification evidence and administrator notes
            <textarea rows="4" maxLength={2000} required value={evidenceNote}
              onChange={(event) => setEvidenceNote(event.target.value)}
              placeholder="Describe how you independently confirmed this email address or phone number..." />
            <small>At least 10 characters. This note will be recorded in the audit log.</small>
          </label>
          {error && <div className="platform-owner-notice error" role="alert">{error}</div>}
          <div className="platform-owner-modal-actions">
            <button type="button" className="platform-owner-button secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="platform-owner-button primary"
              disabled={saving || evidenceNote.trim().length < 10}>
              {saving ? "Verifying..." : `Confirm ${contactType} verification`}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function PlatformOwnerUserVerification() {
  const [users, setUsers] = useState([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState({ page: 1, pages: 1, total: 0 });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [verificationTarget, setVerificationTarget] = useState(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await platformOwnerApi.listUsers({
        search: search || undefined,
        page,
        per_page: 20,
      });
      setUsers(response.users || []);
      setPagination(response.pagination || { page, pages: 1, total: 0 });
    } catch (error) {
      setLoadError(error.message || "User accounts could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [search, page]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  function submitSearch(event) {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  async function verifyContact(evidenceNote) {
    if (!verificationTarget) return;
    setSaving(true);
    setActionError("");
    setNotice("");
    try {
      await platformOwnerApi.verifyUserContact(
        verificationTarget.user.id,
        verificationTarget.contactType,
        evidenceNote
      );
      const label = verificationTarget.contactType;
      setVerificationTarget(null);
      setNotice(`User ${label} verified successfully. The audit record has been saved.`);
      await loadUsers();
    } catch (error) {
      setActionError(error.message || "Contact verification could not be completed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="platform-owner-page platform-owner-user-verification-page">
      <div className="platform-owner-page-header">
        <div>
          <span className="platform-owner-eyebrow">ACCOUNT SECURITY</span>
          <h1>User verification</h1>
          <p>Review registered email addresses and phone numbers. Verify each contact separately after checking evidence.</p>
        </div>
        <button className="platform-owner-button secondary" onClick={loadUsers} disabled={loading}>
          <RefreshCw size={16} className={loading ? "platform-owner-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="platform-owner-notice">
        <ShieldCheck size={18} />
        <span>Only authorised PesaPilot platform administrators can perform manual verification. Entering a contact detail does not verify it.</span>
      </div>

      {loadError && <div className="platform-owner-notice error" role="alert">{loadError}</div>}
      {actionError && !verificationTarget && (
        <div className="platform-owner-notice error" role="alert">{actionError}</div>
      )}
      {notice && <div className="platform-owner-notice success" role="status">{notice}</div>}

      <form className="platform-owner-toolbar" onSubmit={submitSearch}>
        <input value={searchInput} onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search by email or phone number..." aria-label="Search users" />
        <button className="platform-owner-button primary" type="submit">
          <Search size={16} /> Search
        </button>
      </form>

      <div className="platform-owner-finance-muted">
        {pagination.total || 0} registered user{pagination.total === 1 ? "" : "s"}
      </div>

      <div className="platform-owner-table-wrap">
        <table className="platform-owner-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Email verification</th>
              <th>Phone verification</th>
              <th>Account</th>
              <th>Registered</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>
                  <strong>{user.email || "No email"}</strong>
                  <div className="platform-owner-finance-muted">{user.phone || "No phone number"}</div>
                </td>
                <td>
                  <VerificationStatus verified={user.email_verified} />
                  {!user.email_verified && user.email && (
                    <div style={{ marginTop: 8 }}>
                      <button className="platform-owner-button secondary small" type="button"
                        onClick={() => { setActionError(""); setVerificationTarget({ user, contactType: "email" }); }}>
                        Verify email
                      </button>
                    </div>
                  )}
                </td>
                <td>
                  <VerificationStatus verified={user.phone_verified} />
                  {!user.phone_verified && user.phone && (
                    <div style={{ marginTop: 8 }}>
                      <button className="platform-owner-button secondary small" type="button"
                        onClick={() => { setActionError(""); setVerificationTarget({ user, contactType: "phone" }); }}>
                        Verify phone
                      </button>
                    </div>
                  )}
                </td>
                <td>
                  <span className={`platform-owner-status ${user.is_active ? "platform-owner-status-active" : "platform-owner-status-inactive"}`}>
                    {user.is_active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td>{formatDate(user.created_at)}</td>
              </tr>
            ))}
            {!loading && users.length === 0 && (
              <tr><td colSpan="5">No users found.</td></tr>
            )}
            {loading && (
              <tr><td colSpan="5">Loading user accounts...</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="platform-owner-toolbar" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <span>Page {pagination.page || page} of {Math.max(1, pagination.pages || 1)}</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="platform-owner-button secondary" type="button"
            disabled={loading || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
            Previous
          </button>
          <button className="platform-owner-button secondary" type="button"
            disabled={loading || page >= (pagination.pages || 1)}
            onClick={() => setPage((value) => value + 1)}>
            Next
          </button>
        </div>
      </div>

      {verificationTarget && (
        <VerificationModal
          user={verificationTarget.user}
          contactType={verificationTarget.contactType}
          onClose={() => { if (!saving) setVerificationTarget(null); }}
          onSubmit={verifyContact}
          saving={saving}
          error={actionError}
        />
      )}
    </div>
  );
}
