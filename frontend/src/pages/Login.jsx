import { useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Wallet, Eye, EyeOff, ArrowRight } from "lucide-react";
import { useAuth } from "../context/useAuth";
import { apiRequest } from "../api/client";
import "./AuthPages.css";

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { login, logout } = useAuth();

  const [ownerMode, setOwnerMode] = useState(false);
  const lastLogoTap = useRef(0);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      await login({ email: email.trim(), password });

      if (ownerMode) {
        try {
          const access = await apiRequest("/platform/owner/access");

          if (access.success !== true || access.role !== "owner") {
            throw new Error("Platform Owner access is not available for this account.");
          }
        } catch (ownerError) {
          await logout();

          if (ownerError.status === 403) {
            throw new Error("This account does not have Platform Owner access.");
          }

          if (ownerError.status === 401) {
            throw new Error("Your session could not be verified. Please sign in again.");
          }

          throw new Error(
            ownerError.message || "Unable to verify Platform Owner access."
          );
        }

        navigate("/platform-owner", { replace: true });
        return;
      }

      const destination = location.state?.from;
      const destinationPath = destination?.pathname || "";

      // Customer login must never redirect into the Platform Owner area.
      const isPlatformOwnerDestination =
        destinationPath === "/platform-owner" ||
        destinationPath.startsWith("/platform-owner/");

      const returnTo =
        !isPlatformOwnerDestination &&
        destinationPath.startsWith("/") &&
        !destinationPath.startsWith("//")
          ? `${destinationPath}${destination.search || ""}${destination.hash || ""}`
          : "/";

      navigate(returnTo, { replace: true });
    } catch (err) {
      setError(err.message || "Unable to sign in. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className={`auth-page ${ownerMode ? "auth-owner-mode" : ""}`}>
      <section className="auth-card">
        <button
          type="button"
          className="auth-brand auth-brand-toggle"
          title="Double-tap to switch login mode"
          aria-label="Double-tap PesaPilot logo to switch login mode"
          onClick={() => {
            const now = Date.now();

            if (now - lastLogoTap.current < 450) {
              setOwnerMode((current) => !current);
              setError("");
              lastLogoTap.current = 0;
            } else {
              lastLogoTap.current = now;
            }
          }}
        >
          <span className="auth-brand-icon"><Wallet size={23} /></span>
          <span>
            <strong>PesaPilot</strong>
            <small>{ownerMode ? "PLATFORM ADMINISTRATION" : "YOUR FINANCIAL COMPANION"}</small>
          </span>
        </button>

        <div className="auth-heading">
          <span className="auth-eyebrow">
            {ownerMode ? "RESTRICTED ADMINISTRATOR ACCESS" : "WELCOME BACK"}
          </span>
          <h1>{ownerMode ? "Platform Owner Login" : "Sign in to PesaPilot"}</h1>
          <p>
            {ownerMode
              ? "Sign in with your configured owner account."
              : "Take control of your financial life."}
          </p>
        </div>

        {searchParams.get("registered") === "1" && (
          <div className="auth-success">
            Your account has been created. Sign in to continue.
          </div>
        )}

        {error && <div className="auth-error">{error}</div>}

        <form onSubmit={handleSubmit} className="auth-form">
          <label htmlFor="login-email">Email address</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <label htmlFor="login-password">Password</label>
          <div className="auth-password-field">
            <input
              id="login-password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button
              type="button"
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((value) => !value)}
            >
              {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
            </button>
          </div>

          <button className="auth-submit" type="submit" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
            {!submitting && <ArrowRight size={18} />}
          </button>
        </form>

        {ownerMode ? (
          <p className="auth-switch">
            Double-tap the PesaPilot logo to return to customer login.
          </p>
        ) : (
          <p className="auth-switch">
            Don't have an account? <Link to="/register">Create one</Link>
          </p>
        )}

        <p className="auth-footer">
          Your financial information belongs to you.
        </p>
      </section>
    </main>
  );
}
