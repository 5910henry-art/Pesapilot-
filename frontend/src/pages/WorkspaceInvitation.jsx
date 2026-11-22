import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Mail, Users } from "lucide-react";
import { useAuth } from "../context/useAuth";
import { authApi } from "../api/client";
import "./WorkspaceInvitation.css";

export default function WorkspaceInvitation() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { reloadUser } = useAuth();

  const invitationTokenFromLink = searchParams.get("token") || "";
  const hasInvitationLink = Boolean(invitationTokenFromLink.trim());

  const [token, setToken] = useState(invitationTokenFromLink);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [workspace, setWorkspace] = useState(null);

  async function handleAccept(event) {
    event.preventDefault();
    setError("");

    const invitationToken = token.trim();
    if (!invitationToken) {
      setError("Enter your invitation token to continue.");
      return;
    }

    setSubmitting(true);

    try {
      const result = await authApi.acceptWorkspaceInvitation(invitationToken);

      if (!result?.success || !result.workspace) {
        throw new Error(result?.message || "The invitation could not be accepted.");
      }

      setWorkspace(result.workspace);

      // The membership is already saved by the backend.
      // Refreshing makes the new workspace available in the app.
      try {
        await reloadUser();
      } catch {
        // Keep the successful acceptance visible even if refreshing fails.
      }
    } catch (err) {
      setError(err.message || "Unable to accept this invitation.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="workspace-invitation-page">
      <section className="workspace-invitation-card">
        <div className="workspace-invitation-brand">
          <span className="workspace-invitation-icon"><Users size={23} /></span>
          <strong>PesaPilot</strong>
        </div>

        {workspace ? (
          <>
            <div className="workspace-invitation-success-icon">
              <CheckCircle2 size={34} />
            </div>
            <h1>You're in!</h1>
            <p className="workspace-invitation-description">
              You have successfully joined <strong>{workspace.name}</strong>.
            </p>
            <div className="workspace-invitation-details">
              <span>Workspace role</span>
              <strong>{workspace.role}</strong>
              <span>Currency</span>
              <strong>{workspace.currency}</strong>
            </div>
            <button
              type="button"
              className="workspace-invitation-submit"
              onClick={() => navigate("/", { replace: true })}
            >
              Open dashboard
            </button>
          </>
        ) : (
          <>
            <span className="workspace-invitation-eyebrow">WORKSPACE INVITATION</span>
            <h1>Join a workspace</h1>
            <p className="workspace-invitation-description">
              {hasInvitationLink
                ? "You've been invited to join a workspace. Your invitation link has been recognised. Accept below to continue."
                : "Enter the invitation token shared with you to join the workspace."}
            </p>

            <form onSubmit={handleAccept}>
              <label htmlFor="workspace-invitation-token">
                {hasInvitationLink ? "Invitation token from your link" : "Invitation token"}
              </label>
              <textarea
                id="workspace-invitation-token"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="Paste your invitation token here"
                rows={4}
                autoComplete="off"
                required
              />

              {error && (
                <div className="workspace-invitation-error" role="alert">
                  <Mail size={17} />
                  <span>{error}</span>
                </div>
              )}

              <button
                type="submit"
                className="workspace-invitation-submit"
                disabled={submitting || !token.trim()}
              >
                {submitting ? "Accepting invitation..." : "Accept invitation"}
              </button>
            </form>

            <p className="workspace-invitation-footnote">
              You must sign in with the verified email address the invitation was sent to.
            </p>
          </>
        )}
      </section>
    </main>
  );
}
