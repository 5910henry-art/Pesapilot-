import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  UserRound, Building2, ShieldCheck, Database,
  LogOut, Trash2, CheckCircle2, AlertCircle,
  Smartphone, Mail, CalendarDays, RefreshCw,
  KeyRound, Monitor,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { useAccounts } from "../context/AccountsContext";
import { authApi } from "../api/client";
import "./Settings.css";

function formatDate(value) {
  if (!value) return "Not available";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not available";

  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function formatDateTime(value) {
  if (!value) return "Not available";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not available";

  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function describeDevice(userAgent) {
  if (!userAgent) return "Unknown device";

  const ua = userAgent.toLowerCase();

  const platform = ua.includes("android")
    ? "Android"
    : ua.includes("iphone") || ua.includes("ipad")
      ? "iOS"
      : ua.includes("windows")
        ? "Windows"
        : ua.includes("macintosh") || ua.includes("mac os")
          ? "macOS"
          : ua.includes("linux")
            ? "Linux"
            : "Unknown platform";

  const browser = ua.includes("edg/")
    ? "Edge"
    : ua.includes("firefox/")
      ? "Firefox"
      : ua.includes("chrome/") && !ua.includes("edg/")
        ? "Chrome"
        : ua.includes("safari/") && !ua.includes("chrome/")
          ? "Safari"
          : "Browser";

  return `${browser} on ${platform}`;
}

function SettingsNotice({ message, onDismiss }) {
  if (!message?.text) return null;

  return (
    <div
      className={`settings-notice ${message.type || "success"}`}
      role={message.type === "error" ? "alert" : "status"}
    >
      {message.type === "error"
        ? <AlertCircle size={19} />
        : <CheckCircle2 size={19} />}
      <span>{message.text}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss message"
      >
        ×
      </button>
    </div>
  );
}

export default function Settings() {
  const {
    user,
    profile,
    workspaces,
    activeWorkspace,
    logout,
    reloadUser,
  } = useAuth();

  const navigate = useNavigate();
  const { clearAccountsCache } = useAccounts();
  const [profileMessage, setProfileMessage] = useState(null);
  const [maintenanceMessage, setMaintenanceMessage] = useState(null);
  const [passwordMessage, setPasswordMessage] = useState(null);
  const [sessionsMessage, setSessionsMessage] = useState(null);
  const [deletionActionMessage, setDeletionActionMessage] = useState(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [clearing, setClearing] = useState(false);

  const [deletionEligibility, setDeletionEligibility] = useState(null);
  const [deletionEligibilityLoading, setDeletionEligibilityLoading] = useState(true);
  const [deletionEligibilityError, setDeletionEligibilityError] = useState("");
  const [showDeletionForm, setShowDeletionForm] = useState(false);
  const [deletionPassword, setDeletionPassword] = useState("");
  const [deletionConfirmation, setDeletionConfirmation] = useState("");
  const [deletingAccount, setDeletingAccount] = useState(false);

  const loadDeletionEligibility = useCallback(async () => {
    setDeletionEligibilityLoading(true);
    setDeletionEligibilityError("");

    try {
      const result = await authApi.accountDeletionEligibility();
      setDeletionEligibility(result);
    } catch (error) {
      setDeletionEligibility(null);
      setDeletionEligibilityError(
        error.message || "Account deletion eligibility could not be checked."
      );
    } finally {
      setDeletionEligibilityLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDeletionEligibility();
  }, [loadDeletionEligibility]);

  const [profileForm, setProfileForm] = useState({
    first_name: "",
    last_name: "",
    phone: "",
    preferred_currency: "KES",
  });
  const [currencies, setCurrencies] = useState([]);
  const [currenciesLoading, setCurrenciesLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);

  const [passwordForm, setPasswordForm] = useState({
    current_password: "",
    new_password: "",
    confirm_password: "",
  });
  const [changingPassword, setChangingPassword] = useState(false);

  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [revokingSessionId, setRevokingSessionId] = useState("");
  const [revokingOthers, setRevokingOthers] = useState(false);

  const [workspaceMembers, setWorkspaceMembers] = useState([]);
  const [workspaceInvitations, setWorkspaceInvitations] = useState([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [workspaceMessage, setWorkspaceMessage] = useState(null);
  const [workspaceInviteEmail, setWorkspaceInviteEmail] = useState("");
  const [workspaceInviteRole, setWorkspaceInviteRole] = useState("member");
  const [workspaceInviteBusy, setWorkspaceInviteBusy] = useState(false);
  const [workspaceActionBusy, setWorkspaceActionBusy] = useState("");
  const [newWorkspaceInvite, setNewWorkspaceInvite] = useState(null);

  const activeWorkspaceId = activeWorkspace?.id || "";
  const activeWorkspaceRole = activeWorkspace?.role || "";
  const isBusinessWorkspace = activeWorkspace?.type === "business";
  const canManageWorkspace = ["owner", "admin"].includes(activeWorkspaceRole);
  const canManageBusinessWorkspace =
    isBusinessWorkspace && canManageWorkspace;

  const loadWorkspaceData = useCallback(async () => {
    if (!activeWorkspaceId) {
      setWorkspaceMembers([]);
      setWorkspaceInvitations([]);
      return;
    }

    setWorkspaceLoading(true);
    setWorkspaceError("");

    try {
      const requests = [
        authApi.listWorkspaceMembers(activeWorkspaceId),
        canManageWorkspace
          ? authApi.listWorkspaceInvitations(activeWorkspaceId)
          : Promise.resolve({ invitations: [] }),
      ];

      const [memberResult, invitationResult] = await Promise.all(requests);

      setWorkspaceMembers(memberResult.members || []);
      setWorkspaceInvitations(invitationResult.invitations || []);
    } catch (error) {
      setWorkspaceError(
        error.message || "Workspace members could not be loaded."
      );
    } finally {
      setWorkspaceLoading(false);
    }
  }, [activeWorkspaceId, canManageWorkspace]);

  useEffect(() => {
    loadWorkspaceData();
  }, [loadWorkspaceData]);

  async function handleCreateWorkspaceInvitation(event) {
    event.preventDefault();
    if (!activeWorkspaceId || !canManageWorkspace || workspaceInviteBusy) return;

    setWorkspaceInviteBusy(true);
    setWorkspaceMessage(null);
    setNewWorkspaceInvite(null);

    try {
      const result = await authApi.createWorkspaceInvitation(activeWorkspaceId, {
        email: workspaceInviteEmail.trim(),
        role: workspaceInviteRole,
      });
      setNewWorkspaceInvite(result.invitation || null);
      setWorkspaceInviteEmail("");
      setWorkspaceMessage({
        text: "Invitation created. Copy and share the invitation link with the invited person.",
        type: "success",
      });
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "The workspace invitation could not be created.",
        type: "error",
      });
    } finally {
      setWorkspaceInviteBusy(false);
    }
  }

  async function handleResendWorkspaceInvitation(invitationId) {
    if (!activeWorkspaceId || !canManageWorkspace || workspaceActionBusy) return;

    setWorkspaceActionBusy(invitationId);
    setWorkspaceMessage(null);
    setNewWorkspaceInvite(null);

    try {
      const result = await authApi.resendWorkspaceInvitation(
        activeWorkspaceId, invitationId
      );
      setNewWorkspaceInvite(result.invitation || null);
      setWorkspaceMessage({
        text: "A new invitation token has been generated. The previous token is no longer valid.",
        type: "success",
      });
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "The invitation could not be resent.",
        type: "error",
      });
    } finally {
      setWorkspaceActionBusy("");
    }
  }

  async function handleRevokeWorkspaceInvitation(invitationId) {
    if (!activeWorkspaceId || !canManageWorkspace || workspaceActionBusy) return;
    if (!window.confirm("Revoke this workspace invitation?")) return;

    setWorkspaceActionBusy(invitationId);
    setWorkspaceMessage(null);

    try {
      const result = await authApi.revokeWorkspaceInvitation(
        activeWorkspaceId, invitationId
      );
      setWorkspaceMessage({
        text: result.message || "Invitation revoked.",
        type: "success",
      });
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "The invitation could not be revoked.",
        type: "error",
      });
    } finally {
      setWorkspaceActionBusy("");
    }
  }

  async function handleWorkspaceMemberRoleChange(member, role) {
    if (!activeWorkspaceId || !canManageWorkspace || workspaceActionBusy) return;

    setWorkspaceActionBusy(member.user_id);
    setWorkspaceMessage(null);

    try {
      await authApi.updateWorkspaceMemberRole(
        activeWorkspaceId, member.user_id, role
      );
      setWorkspaceMessage({
        text: "Workspace member role updated.",
        type: "success",
      });
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "The member role could not be updated.",
        type: "error",
      });
    } finally {
      setWorkspaceActionBusy("");
    }
  }

  async function handleRemoveWorkspaceMember(member) {
    if (!activeWorkspaceId || !canManageWorkspace || workspaceActionBusy) return;
    if (!window.confirm(
      `Remove ${member.email} from this workspace? Their personal account will remain active.`
    )) return;

    setWorkspaceActionBusy(member.user_id);
    setWorkspaceMessage(null);

    try {
      const result = await authApi.removeWorkspaceMember(
        activeWorkspaceId, member.user_id
      );
      setWorkspaceMessage({
        text: result.message || "Workspace member removed.",
        type: "success",
      });
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "The member could not be removed.",
        type: "error",
      });
    } finally {
      setWorkspaceActionBusy("");
    }
  }

  async function handleTransferWorkspaceOwnership(member) {
    if (
      !activeWorkspaceId ||
      activeWorkspaceRole !== "owner" ||
      member.role !== "admin" ||
      workspaceActionBusy
    ) return;

    const confirmed = window.confirm(
      `Transfer ownership of ${activeWorkspace?.name || "this workspace"} to ${member.email}? You will become an admin and lose owner-only controls.`
    );
    if (!confirmed) return;

    setWorkspaceActionBusy(`transfer:${member.user_id}`);
    setWorkspaceMessage(null);

    try {
      const result = await authApi.transferWorkspaceOwnership(
        activeWorkspaceId,
        member.user_id
      );

      setWorkspaceMessage({
        text: result.message || "Workspace ownership transferred.",
        type: "success",
      });

      await reloadUser();
      await loadWorkspaceData();
    } catch (error) {
      setWorkspaceMessage({
        text: error.message || "Workspace ownership could not be transferred.",
        type: "error",
      });
    } finally {
      setWorkspaceActionBusy("");
    }
  }

  useEffect(() => {
    setProfileForm({
      first_name: profile?.first_name || "",
      last_name: profile?.last_name || "",
      phone: user?.phone || "",
      preferred_currency: profile?.preferred_currency || "KES",
    });
  }, [
    profile?.first_name,
    profile?.last_name,
    profile?.preferred_currency,
    user?.phone,
  ]);

  useEffect(() => {
    let mounted = true;

    async function loadCurrencies() {
      try {
        const result = await authApi.listCurrencies();
        if (mounted) {
          setCurrencies(result.currencies || []);
        }
      } catch (error) {
        if (mounted) {
          showCurrencyLoadError(error);
        }
      } finally {
        if (mounted) setCurrenciesLoading(false);
      }
    }

    function showCurrencyLoadError() {
      setCurrencies([]);
    }

    loadCurrencies();

    return () => {
      mounted = false;
    };
  }, []);

  function updateProfileField(event) {
    const { name, value } = event.target;
    setProfileForm((current) => ({
      ...current,
      [name]: value,
    }));
  }

  async function saveProfile(event) {
    event.preventDefault();

    if (savingProfile) return;

    setSavingProfile(true);
    setProfileMessage(null);

    try {
      await authApi.updateProfile({
        first_name: profileForm.first_name,
        last_name: profileForm.last_name,
        phone: profileForm.phone.trim() || null,
        preferred_currency: profileForm.preferred_currency,
      });

      await reloadUser();

      setProfileMessage({ text: "Your profile has been updated successfully.", type: "success" });
    } catch (error) {
      setProfileMessage({
        text: error.message || "Your profile could not be updated.",
        type: "error",
      });
    } finally {
      setSavingProfile(false);
    }
  }

  const fullName = [
    profile?.first_name,
    profile?.last_name,
  ].filter(Boolean).join(" ") || "PesaPilot user";

  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);

    try {
      const result = await authApi.listSessions();
      setSessions(result.sessions || []);
    } catch (error) {
      setSessionsMessage({
        text: error.message || "Active sessions could not be loaded.",
        type: "error",
      });
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  function updatePasswordField(event) {
    const { name, value } = event.target;
    setPasswordForm((current) => ({
      ...current,
      [name]: value,
    }));
  }

  async function handleChangePassword(event) {
    event.preventDefault();

    if (changingPassword) return;

    if (passwordForm.new_password.length < 12 ||
        passwordForm.new_password.length > 128) {
      setPasswordMessage({ text: "New password must be between 12 and 128 characters.", type: "error" });
      return;
    }

    if (passwordForm.new_password !== passwordForm.confirm_password) {
      setPasswordMessage({ text: "The new password and confirmation do not match.", type: "error" });
      return;
    }

    setChangingPassword(true);
    setPasswordMessage(null);

    try {
      await authApi.changePassword({
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
      });
    } catch (error) {
      setPasswordMessage({
        text: error.message || "Your password could not be changed.",
        type: "error",
      });
      setChangingPassword(false);
      return;
    }

    setPasswordForm({
      current_password: "",
      new_password: "",
      confirm_password: "",
    });

    setPasswordMessage({ text: "Password changed. Signing you out for security.", type: "success" });

    try {
      await logout();
    } finally {
      navigate("/login", { replace: true });
    }
  }

  async function handleRevokeSession(sessionId) {
    if (revokingSessionId) return;

    setRevokingSessionId(sessionId);
    setSessionsMessage(null);

    try {
      const result = await authApi.revokeSession(sessionId);
      setSessionsMessage({ text: result.message || "Session revoked successfully.", type: "success" });
      await loadSessions();
    } catch (error) {
      setSessionsMessage({ text: error.message || "Session could not be revoked.", type: "error" });
    } finally {
      setRevokingSessionId("");
    }
  }

  async function handleRevokeOtherSessions() {
    if (revokingOthers) return;

    setRevokingOthers(true);
    setSessionsMessage(null);

    try {
      const result = await authApi.revokeOtherSessions();
      setSessionsMessage({ text: result.message || "Other sessions have been revoked.", type: "success" });
      await loadSessions();
    } catch (error) {
      setSessionsMessage({ text: error.message || "Other sessions could not be revoked.", type: "error" });
    } finally {
      setRevokingOthers(false);
    }
  }

  async function handleDeleteAccount(event) {
    event.preventDefault();

    if (
      deletingAccount ||
      deletionEligibility?.eligible !== true ||
      !deletionPassword ||
      deletionConfirmation !== "DELETE"
    ) {
      return;
    }

    setDeletingAccount(true);
    setDeletionActionMessage(null);

    try {
      const result = await authApi.deleteAccount({
        password: deletionPassword,
        confirmation: "DELETE",
      });

      if (!result?.success) {
        throw new Error(
          result?.message || "Your account could not be deleted."
        );
      }

      try {
        await clearAccountsCache();
      } catch (cacheError) {
        // Cache cleanup must not prevent logout after confirmed deletion.
      }

      try {
        await logout();
      } catch (logoutError) {
        // The account is already deleted; always clear local auth and redirect.
      } finally {
        navigate("/login", {
          replace: true,
          state: {
            message:
              result.message ||
              "Your PesaPilot account has been permanently deleted.",
          },
        });
      }
    } catch (error) {
      setDeletionActionMessage({
        text: error.message || "Account deletion failed. Please try again.",
        type: "error",
      });

      setDeletionPassword("");
      setDeletionConfirmation("");

      if (error.status === 409) {
        await loadDeletionEligibility();
        setShowDeletionForm(false);
      }
    } finally {
      setDeletingAccount(false);
    }
  }

  async function clearAppCache() {
    if (clearing) return;

    setClearing(true);
    setMaintenanceMessage(null);

    try {
      await clearAccountsCache();

      let removed = 0;

      // Preserve authentication and workspace selection.
      for (const key of Object.keys(localStorage)) {
        if (
          key.startsWith("pesapilot.cache.") ||
          key.startsWith("pesapilot.preference.")
        ) {
          localStorage.removeItem(key);
          removed += 1;
        }
      }

      if ("caches" in window) {
        const cacheNames = await window.caches.keys();
        const appCaches = cacheNames.filter((name) =>
          name.startsWith("pesapilot-")
        );

        const results = await Promise.all(
          appCaches.map((name) => window.caches.delete(name))
        );

        removed += results.filter(Boolean).length;
      }

      setMaintenanceMessage({
        text: `Cleared the in-memory account cache and refreshed account data. Removed ${removed} additional local cache or preference item(s). Your login, selected workspace and server-side financial records were preserved.`,
        type: "success",
      });
    } catch {
      setMaintenanceMessage({
        text: "The local account cache was cleared, but fresh account data could not be loaded. Check your connection and try refreshing the Accounts page.",
        type: "error",
      });
    } finally {
      setClearing(false);
    }
  }

  async function handleLogout() {
    if (loggingOut) return;

    setLoggingOut(true);

    try {
      await logout();
      navigate("/login", { replace: true });
    } catch {
      setSessionsMessage({
        text: "Sign out could not be completed. Please try again.",
        type: "error",
      });
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <main className="settings-page">
      <header className="settings-page-header">
        <div>
          <span className="settings-eyebrow">PESAPILOT PREFERENCES</span>
          <h1>Settings</h1>
          <p>Manage your account, workspaces, security and application data.</p>
        </div>
      </header>

      <section className="settings-section">
        <div className="settings-section-heading">
          <UserRound size={20} />
          <div>
            <h2>My account</h2>
            <p>Your personal PesaPilot account information.</p>
          </div>
        </div>

        <div className="settings-panel">
          <div className="settings-account-summary">
            <div className="settings-avatar">
              {fullName.charAt(0).toUpperCase()}
            </div>
            <div>
              <h3>{fullName}</h3>
              <p>{user?.email || "Email unavailable"}</p>
              <span className="settings-status">
                <CheckCircle2 size={14} />
                {user?.is_active ? "Active account" : "Inactive account"}
              </span>
            </div>
          </div>

          <div className="settings-details-grid">
            <div className="settings-detail">
              <Mail size={17} />
              <div>
                <span>Email address</span>
                <strong>{user?.email || "Not provided"}</strong>
                <small>{user?.email_verified ? "Verified" : "Not verified"}</small>
              </div>
            </div>

            <div className="settings-detail">
              <Smartphone size={17} />
              <div>
                <span>Phone number</span>
                <strong>{user?.phone || "Not provided"}</strong>
                <small>{user?.phone_verified ? "Verified" : "Not verified"}</small>
              </div>
            </div>

            <div className="settings-detail">
              <CalendarDays size={17} />
              <div>
                <span>Member since</span>
                <strong>{formatDate(user?.created_at)}</strong>
              </div>
            </div>

            <div className="settings-detail">
              <ShieldCheck size={17} />
              <div>
                <span>Preferred currency</span>
                <strong>{profile?.preferred_currency || "Not set"}</strong>
              </div>
            </div>
          </div>

          <form className="settings-profile-form" onSubmit={saveProfile}>
            <SettingsNotice
              message={profileMessage}
              onDismiss={() => setProfileMessage(null)}
            />
            <div className="settings-form-heading">
              <h3>Edit personal information</h3>
              <p>Update the details associated with your PesaPilot account.</p>
            </div>

            <div className="settings-form-grid">
              <label className="settings-field">
                <span>First name</span>
                <input
                  type="text"
                  name="first_name"
                  value={profileForm.first_name}
                  onChange={updateProfileField}
                  maxLength={100}
                  autoComplete="given-name"
                  required
                />
              </label>

              <label className="settings-field">
                <span>Last name</span>
                <input
                  type="text"
                  name="last_name"
                  value={profileForm.last_name}
                  onChange={updateProfileField}
                  maxLength={100}
                  autoComplete="family-name"
                  required
                />
              </label>

              <label className="settings-field">
                <span>Phone number</span>
                <input
                  type="tel"
                  name="phone"
                  value={profileForm.phone}
                  onChange={updateProfileField}
                  placeholder="+254712345678"
                  autoComplete="tel"
                />
                <small>Use international format, including the country code.</small>
              </label>

              <label className="settings-field">
                <span>Preferred currency</span>
                <select
                  name="preferred_currency"
                  value={profileForm.preferred_currency}
                  onChange={updateProfileField}
                  disabled={currenciesLoading || currencies.length === 0}
                  required
                >
                  {currencies.length === 0 && (
                    <option value={profileForm.preferred_currency}>
                      {currenciesLoading ? "Loading currencies..." : "Currencies unavailable"}
                    </option>
                  )}
                  {currencies.map((currency) => (
                    <option key={currency.code} value={currency.code}>
                      {currency.code} — {currency.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <p className="settings-form-warning">
              Changing your phone number resets its verification status.
              You may need to verify the new number again.
            </p>

            <div className="settings-form-footer">
              <p>Email address cannot be changed here because it requires a separate verification process.</p>
              <button
                type="submit"
                className="settings-button primary"
                disabled={
                  savingProfile ||
                  currenciesLoading ||
                  currencies.length === 0
                }
              >
                {savingProfile ? "Saving..." : "Save changes"}
              </button>
            </div>
          </form>
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <Building2 size={20} />
          <div>
            <h2>Workspaces</h2>
            <p>Your personal and business financial workspaces.</p>
          </div>
        </div>

        <div className="settings-workspace-list">
          {workspaces.length ? workspaces.map((workspace) => {
            const isActive = workspace.id === activeWorkspace?.id;

            return (
              <article className="settings-workspace" key={workspace.id}>
                <div className="settings-workspace-icon">
                  <Building2 size={20} />
                </div>
                <div className="settings-workspace-info">
                  <h3>{workspace.name}</h3>
                  <p>
                    {workspace.type === "business" ? "Business" : "Personal"}
                    {" · "}
                    {workspace.currency || "Currency not set"}
                    {" · "}
                    {workspace.role || "Member"}
                  </p>
                </div>
                {isActive && (
                  <span className="settings-active-label">Active</span>
                )}
              </article>
            );
          }) : (
            <div className="settings-empty">
              No workspaces are currently available.
            </div>
          )}
        </div>

        <p className="settings-helper">
          Switch workspaces using the workspace selector in your dashboard.
        </p>

        <div className="settings-workspace-management">
          <div className="settings-form-heading">
            <h3>Workspace members</h3>
            <p>People who can access this workspace and their assigned roles.</p>
          </div>

          {workspaceError && (
            <div className="settings-notice error">{workspaceError}</div>
          )}

          {workspaceMessage && (
            <div className={`settings-notice ${workspaceMessage.type || "success"}`}>
              {workspaceMessage.text}
            </div>
          )}

          {workspaceLoading ? (
            <div className="settings-empty">Loading workspace members...</div>
          ) : workspaceMembers.length ? (
            <div className="settings-workspace-list">
              {workspaceMembers.map((member) => {
                const manageable =
                  activeWorkspaceRole === "owner"
                    ? member.role !== "owner"
                    : activeWorkspaceRole === "admin" &&
                      ["member", "viewer"].includes(member.role);

                return (
                  <article className="settings-workspace" key={member.user_id}>
                    <div className="settings-workspace-icon">
                      <UserRound size={19} />
                    </div>
                    <div className="settings-workspace-info">
                      <h3>{member.email}</h3>
                      <p>
                        {member.role} · Joined {formatDate(member.joined_at)}
                      </p>
                    </div>

                    {manageable && canManageWorkspace && (
                      <div className="settings-workspace-actions">
                        {isBusinessWorkspace &&
                          activeWorkspaceRole === "owner" &&
                          member.role === "admin" && (
                          <button
                            type="button"
                            className="settings-button secondary"
                            disabled={Boolean(workspaceActionBusy)}
                            onClick={() => handleTransferWorkspaceOwnership(member)}
                          >
                            Transfer ownership
                          </button>
                        )}
                        {isBusinessWorkspace && (
                          <select
                            aria-label={`Role for ${member.email}`}
                            value={member.role}
                            disabled={Boolean(workspaceActionBusy)}
                            onChange={(event) =>
                              handleWorkspaceMemberRoleChange(member, event.target.value)
                            }
                          >
                            {activeWorkspaceRole === "owner" && (
                              <option value="admin">Admin</option>
                            )}
                            <option value="member">Member</option>
                            <option value="viewer">Viewer</option>
                          </select>
                        )}
                        <button
                          type="button"
                          className="settings-button danger"
                          disabled={Boolean(workspaceActionBusy)}
                          onClick={() => handleRemoveWorkspaceMember(member)}
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="settings-empty">
              No workspace members were found.
            </div>
          )}

          {canManageWorkspace && (
            <>
              {isBusinessWorkspace && (
                <>
              <div className="settings-form-heading">
                <h3>Invite a member</h3>
                <p>
                  Invitations expire after seven days. You will need to share
                  the generated invitation link with the invited person.
                </p>
              </div>

              <form
                className="settings-form-grid"
                onSubmit={handleCreateWorkspaceInvitation}
              >
                <label className="settings-field">
                  <span>Email address</span>
                  <input
                    type="email"
                    value={workspaceInviteEmail}
                    onChange={(event) => setWorkspaceInviteEmail(event.target.value)}
                    placeholder="person@example.com"
                    maxLength={255}
                    required
                  />
                </label>

                <label className="settings-field">
                  <span>Workspace role</span>
                  <select
                    value={workspaceInviteRole}
                    onChange={(event) => setWorkspaceInviteRole(event.target.value)}
                  >
                    {activeWorkspaceRole === "owner" && (
                      <option value="admin">Admin</option>
                    )}
                    <option value="member">Member</option>
                    <option value="viewer">Viewer</option>
                  </select>
                </label>

                <button
                  type="submit"
                  className="settings-button"
                  disabled={workspaceInviteBusy || !activeWorkspaceId}
                >
                  {workspaceInviteBusy ? "Creating invitation..." : "Create invitation"}
                </button>
              </form>

              {newWorkspaceInvite?.token && (
                <div className="settings-workspace-token">
                  <strong>Invitation link</strong>
                  <p>Copy this link and share it through WhatsApp or SMS. It is only available here once.</p>
                  <input
                    type="text"
                    value={`${window.location.origin}/workspace-invitation?token=${encodeURIComponent(newWorkspaceInvite.token)}`}
                    readOnly
                    onFocus={(event) => event.target.select()}
                    aria-label="New workspace invitation token"
                  />
                  <button
                    type="button"
                    className="settings-button secondary"
                    onClick={async () => {
                      const value = `${window.location.origin}/workspace-invitation?token=${encodeURIComponent(newWorkspaceInvite.token)}`;

                      try {
                        if (navigator.clipboard?.writeText) {
                          await navigator.clipboard.writeText(value);
                        } else {
                          const temporary = document.createElement("textarea");
                          temporary.value = value;
                          temporary.setAttribute("readonly", "");
                          temporary.style.position = "fixed";
                          temporary.style.opacity = "0";
                          document.body.appendChild(temporary);
                          temporary.select();
                          const copied = document.execCommand("copy");
                          document.body.removeChild(temporary);

                          if (!copied) {
                            throw new Error("Clipboard access is unavailable.");
                          }
                        }

                        setWorkspaceMessage({
                          type: "success",
                          text: "Invitation link copied. Send it to the invited person.",
                        });
                      } catch {
                        setWorkspaceMessage({
                          type: "error",
                          text: "Could not copy automatically. Tap the invitation link field to select it, then copy it manually.",
                        });
                      }
                    }}
                  >
                    Copy invitation link
                  </button>
                </div>
              )}

                </>
              )}

              {!isBusinessWorkspace && (
                <div className="settings-notice">
                  Personal workspaces are private. Existing pending invitations
                  cannot be accepted, but you can revoke them here. Create a
                  Business/SACCO workspace to invite members and collaborate.
                </div>
              )}

              <div className="settings-form-heading">
                <h3>Pending invitations</h3>
                <p>Manage invitations that have not yet been accepted.</p>
              </div>

              {workspaceInvitations.length ? (
                <div className="settings-workspace-list">
                  {workspaceInvitations.map((invitation) => (
                    <article className="settings-workspace" key={invitation.id}>
                      <div className="settings-workspace-icon">
                        <Mail size={19} />
                      </div>
                      <div className="settings-workspace-info">
                        <h3>{invitation.email}</h3>
                        <p>
                          {invitation.role} · {invitation.status} · Expires{" "}
                          {formatDate(invitation.expires_at)}
                        </p>
                      </div>
                      {["pending", "expired"].includes(invitation.status) && (
                        <div className="settings-workspace-actions">
                          {isBusinessWorkspace && (
                            <button
                              type="button"
                              className="settings-button secondary"
                              disabled={Boolean(workspaceActionBusy)}
                              onClick={() =>
                                handleResendWorkspaceInvitation(invitation.id)
                              }
                            >
                              Resend
                            </button>
                          )}
                          <button
                            type="button"
                            className="settings-button danger"
                            disabled={Boolean(workspaceActionBusy)}
                            onClick={() =>
                              handleRevokeWorkspaceInvitation(invitation.id)
                            }
                          >
                            Revoke
                          </button>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="settings-empty">
                  There are no workspace invitations.
                </div>
              )}
            </>
          )}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <Database size={20} />
          <div>
            <h2>App maintenance</h2>
            <p>Manage locally stored PesaPilot preferences and cache.</p>
          </div>
        </div>

        <div className="settings-action-row">
          <div className="settings-action-icon">
            <RefreshCw size={19} />
          </div>
          <div className="settings-action-copy">
            <h3>Clear app cache</h3>
            <p>
              Remove PesaPilot-specific cached preferences. Your login,
              workspace selection and financial records will be preserved.
            </p>
          </div>
          <button
            type="button"
            className="settings-button secondary"
            onClick={clearAppCache}
            disabled={clearing}
          >
            {clearing ? "Clearing..." : "Clear cache"}
          </button>
        </div>
        <SettingsNotice
          message={maintenanceMessage}
          onDismiss={() => setMaintenanceMessage(null)}
        />
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <KeyRound size={20} />
          <div>
            <h2>Change password</h2>
            <p>Update your password to protect your account.</p>
          </div>
        </div>

        <div className="settings-panel">
          <form className="settings-profile-form settings-password-form"
            onSubmit={handleChangePassword}>
            <SettingsNotice
              message={passwordMessage}
              onDismiss={() => setPasswordMessage(null)}
            />
            <div className="settings-form-grid">
              <label className="settings-field">
                <span>Current password</span>
                <input
                  type="password"
                  name="current_password"
                  value={passwordForm.current_password}
                  onChange={updatePasswordField}
                  autoComplete="current-password"
                  required
                />
              </label>

              <label className="settings-field">
                <span>New password</span>
                <input
                  type="password"
                  name="new_password"
                  value={passwordForm.new_password}
                  onChange={updatePasswordField}
                  minLength={12}
                  maxLength={128}
                  autoComplete="new-password"
                  required
                />
                <small>Use between 12 and 128 characters.</small>
              </label>

              <label className="settings-field">
                <span>Confirm new password</span>
                <input
                  type="password"
                  name="confirm_password"
                  value={passwordForm.confirm_password}
                  onChange={updatePasswordField}
                  minLength={12}
                  maxLength={128}
                  autoComplete="new-password"
                  required
                />
              </label>
            </div>

            <div className="settings-form-footer">
              <p>Changing your password signs out all devices, including this one.</p>
              <button
                type="submit"
                className="settings-button primary"
                disabled={
                  changingPassword ||
                  !passwordForm.current_password ||
                  !passwordForm.new_password ||
                  !passwordForm.confirm_password
                }
              >
                {changingPassword ? "Changing..." : "Change password"}
              </button>
            </div>
          </form>
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <ShieldCheck size={20} />
          <div>
            <h2>Security and sessions</h2>
            <p>Control access to your current PesaPilot session.</p>
          </div>
        </div>

        <div className="settings-panel settings-sessions-panel">
          <SettingsNotice
            message={sessionsMessage}
            onDismiss={() => setSessionsMessage(null)}
          />
          <div className="settings-sessions-heading">
            <div>
              <h3>Where you're signed in</h3>
              <p>Review devices that currently have access to your account.</p>
            </div>
            <button
              type="button"
              className="settings-button secondary"
              onClick={loadSessions}
              disabled={sessionsLoading}
            >
              <RefreshCw size={15} />
              {sessionsLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {sessionsLoading ? (
            <div className="settings-empty">Loading active sessions...</div>
          ) : sessions.length === 0 ? (
            <div className="settings-empty">
              No active sessions were returned.
            </div>
          ) : (
            <div className="settings-session-list">
              {sessions.map((session) => (
                <article className="settings-session" key={session.session_id}>
                  <div className="settings-session-icon">
                    {session.user_agent?.toLowerCase().includes("mobile")
                      ? <Smartphone size={20} />
                      : <Monitor size={20} />}
                  </div>

                  <div className="settings-session-info">
                    <div className="settings-session-title">
                      <h3>{describeDevice(session.user_agent)}</h3>
                      {session.is_current && (
                        <span className="settings-active-label">This device</span>
                      )}
                    </div>
                    <p>
                      IP address: {session.ip_address || "Not available"}
                    </p>
                    <p>
                      Last activity: {formatDateTime(session.last_seen_at)}
                    </p>
                    <small>
                      First seen: {formatDateTime(session.created_at)}
                    </small>
                  </div>

                  {!session.is_current && (
                    <button
                      type="button"
                      className="settings-button danger"
                      onClick={() => handleRevokeSession(session.session_id)}
                      disabled={Boolean(revokingSessionId) || revokingOthers}
                    >
                      {revokingSessionId === session.session_id
                        ? "Revoking..."
                        : "Revoke"}
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}

          {sessions.some((session) => !session.is_current) && (
            <div className="settings-sessions-footer">
              <p>Sign out every other device while keeping this one active.</p>
              <button
                type="button"
                className="settings-button danger"
                onClick={handleRevokeOtherSessions}
                disabled={revokingOthers || Boolean(revokingSessionId)}
              >
                {revokingOthers ? "Revoking..." : "Sign out other devices"}
              </button>
            </div>
          )}
        </div>

        <div className="settings-action-row">
          <div className="settings-action-icon">
            <LogOut size={19} />
          </div>
          <div className="settings-action-copy">
            <h3>Sign out</h3>
            <p>End your current session on this device.</p>
          </div>
          <button
            type="button"
            className="settings-button secondary"
            onClick={handleLogout}
            disabled={loggingOut}
          >
            {loggingOut ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </section>

      <section className="settings-section settings-danger-section">
        <div className="settings-section-heading">
          <Trash2 size={20} />
          <div>
            <h2>Permanent account deletion</h2>
            <p>Close your PesaPilot account and remove your personal workspace.</p>
          </div>
        </div>

        <div className="settings-danger-panel">
          <div>
            <h3>Close my PesaPilot account</h3>
            <p>
              This action permanently deletes your account and cannot be undone.
              Your personal workspace and its records will be deleted. Shared
              workspaces and other members' records will remain intact.
            </p>
          </div>
          <button
            type="button"
            className="settings-button danger"
            onClick={loadDeletionEligibility}
            disabled={deletionEligibilityLoading || deletingAccount}
          >
            <RefreshCw size={16} />
            {deletionEligibilityLoading ? "Checking..." : "Refresh eligibility"}
          </button>
        </div>

        <div className="settings-deletion-details">
          <div className="settings-deletion-warning">
            <AlertCircle size={18} />
            <div>
              <strong>Important: financial records are retained.</strong>
              <p>
                Historical PesaPilot loan, repayment, financial and audit
                records may be retained where required. Personal identifying
                details in retained platform records are anonymised.
              </p>
            </div>
          </div>

          <SettingsNotice
            message={deletionActionMessage}
            onDismiss={() => setDeletionActionMessage(null)}
          />

          {deletionEligibilityLoading && (
            <p className="settings-deletion-status" role="status">
              Checking whether your account is eligible for permanent deletion...
            </p>
          )}

          {!deletionEligibilityLoading && deletionEligibilityError && (
            <div className="settings-deletion-status error" role="alert">
              <p>{deletionEligibilityError}</p>
              <button
                type="button"
                className="settings-button secondary"
                onClick={loadDeletionEligibility}
              >
                Try again
              </button>
            </div>
          )}

          {!deletionEligibilityLoading && deletionEligibility && (
            <>
              {deletionEligibility.eligible === true ? (
                <div className="settings-deletion-status eligible">
                  <CheckCircle2 size={18} />
                  <span>
                    Your account is currently eligible for permanent deletion.
                  </span>
                </div>
              ) : (
                <div className="settings-deletion-status blocked">
                  <AlertCircle size={18} />
                  <span>
                    Your account cannot currently be deleted. Resolve the
                    following item(s) first:
                  </span>
                </div>
              )}

              {Array.isArray(deletionEligibility.blockers) &&
                deletionEligibility.blockers.length > 0 && (
                  <ul className="settings-deletion-blockers">
                    {deletionEligibility.blockers.map((blocker, index) => (
                      <li key={`${blocker.code || "blocker"}-${index}`}>
                        <strong>{blocker.message || "Deletion requirement not met."}</strong>
                        {Number(blocker.count) > 0 && (
                          <span> ({blocker.count})</span>
                        )}
                        {Array.isArray(blocker.workspaces) &&
                          blocker.workspaces.length > 0 && (
                            <ul>
                              {blocker.workspaces.map((workspace) => (
                                <li key={workspace.workspace_id}>
                                  {workspace.workspace_name || "Shared workspace"}
                                </li>
                              ))}
                            </ul>
                          )}
                      </li>
                    ))}
                  </ul>
                )}
            </>
          )}

          {deletionEligibility?.eligible === true && !showDeletionForm && (
            <button
              type="button"
              className="settings-button danger settings-deletion-start"
              onClick={() => setShowDeletionForm(true)}
              disabled={deletionEligibilityLoading || deletingAccount}
            >
              <Trash2 size={16} />
              Continue to permanent deletion
            </button>
          )}

          {showDeletionForm && deletionEligibility?.eligible === true && (
            <form
              className="settings-deletion-form"
              onSubmit={handleDeleteAccount}
            >
              <div className="settings-form-heading">
                <h3>Confirm permanent account deletion</h3>
                <p>
                  Enter your current password and type DELETE exactly. This
                  confirmation is required before the deletion request is sent.
                </p>
              </div>

              <label className="settings-field">
                <span>Current password</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={deletionPassword}
                  onChange={(event) => setDeletionPassword(event.target.value)}
                  required
                  disabled={deletingAccount}
                />
              </label>

              <label className="settings-field">
                <span>Type DELETE to confirm</span>
                <input
                  type="text"
                  autoComplete="off"
                  value={deletionConfirmation}
                  onChange={(event) => setDeletionConfirmation(event.target.value)}
                  placeholder="DELETE"
                  required
                  disabled={deletingAccount}
                />
              </label>

              <p className="settings-form-warning">
                This action is permanent. You will be signed out after successful
                deletion. Outstanding platform loans, pending loan activity,
                shared workspace ownership and being the last active platform
                administrator can prevent deletion.
              </p>

              <div className="settings-deletion-actions">
                <button
                  type="button"
                  className="settings-button secondary"
                  onClick={() => {
                    setShowDeletionForm(false);
                    setDeletionPassword("");
                    setDeletionConfirmation("");
                  }}
                  disabled={deletingAccount}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="settings-button danger"
                  disabled={
                    deletingAccount ||
                    !deletionPassword ||
                    deletionConfirmation !== "DELETE"
                  }
                >
                  <Trash2 size={16} />
                  {deletingAccount ? "Deleting account..." : "Permanently delete account"}
                </button>
              </div>
            </form>
          )}
        </div>
      </section>
    </main>
  );
}
