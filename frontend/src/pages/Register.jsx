import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Wallet, Eye, EyeOff, ArrowRight } from "lucide-react";
import { useAuth } from "../context/useAuth";
import "./AuthPages.css";

export default function Register() {
  const navigate = useNavigate();
  const { register } = useAuth();

  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    password: "",
    confirm_password: "",
    currency: "KES",
  });

  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (form.password !== form.confirm_password) {
      setError("Your passwords do not match.");
      return;
    }

    if (form.password.length < 12 || form.password.length > 128) {
      setError("Password must contain between 12 and 128 characters.");
      return;
    }

    setSubmitting(true);

    try {
      await register({
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim() || null,
        password: form.password,
        currency: form.currency,
      });

      navigate("/login?registered=1", { replace: true });
    } catch (err) {
      setError(err.message || "Registration failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card auth-card-wide">
        <Link to="/login" className="auth-brand">
          <span className="auth-brand-icon"><Wallet size={23} /></span>
          <span>
            <strong>PesaPilot</strong>
            <small>YOUR FINANCIAL COMPANION</small>
          </span>
        </Link>

        <div className="auth-heading">
          <span className="auth-eyebrow">GET STARTED</span>
          <h1>Create your account</h1>
          <p>One secure place for your entire financial life.</p>
        </div>

        {error && <div className="auth-error">{error}</div>}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="auth-form-grid">
            <div>
              <label htmlFor="first-name">First name</label>
              <input
                id="first-name"
                name="first_name"
                autoComplete="given-name"
                placeholder="First name"
                value={form.first_name}
                onChange={updateField}
                maxLength={100}
                required
              />
            </div>

            <div>
              <label htmlFor="last-name">Last name</label>
              <input
                id="last-name"
                name="last_name"
                autoComplete="family-name"
                placeholder="Last name"
                value={form.last_name}
                onChange={updateField}
                maxLength={100}
                required
              />
            </div>
          </div>

          <label htmlFor="register-email">Email address</label>
          <input
            id="register-email"
            name="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={form.email}
            onChange={updateField}
            maxLength={255}
            required
          />

          <label htmlFor="register-phone">
            Phone number <span className="auth-optional">Optional</span>
          </label>
          <input
            id="register-phone"
            name="phone"
            type="tel"
            autoComplete="tel"
            placeholder="+254712345678"
            value={form.phone}
            onChange={updateField}
          />

          <label htmlFor="register-currency">Main currency</label>
          <select
            id="register-currency"
            name="currency"
            value={form.currency}
            onChange={updateField}
          >
            <option value="KES">KES — Kenyan Shilling</option>
            <option value="USD">USD — US Dollar</option>
            <option value="EUR">EUR — Euro</option>
            <option value="GBP">GBP — British Pound</option>
            <option value="UGX">UGX — Ugandan Shilling</option>
            <option value="TZS">TZS — Tanzanian Shilling</option>
            <option value="RWF">RWF — Rwandan Franc</option>
            <option value="ZAR">ZAR — South African Rand</option>
            <option value="NGN">NGN — Nigerian Naira</option>
            <option value="GHS">GHS — Ghanaian Cedi</option>
            <option value="INR">INR — Indian Rupee</option>
            <option value="CAD">CAD — Canadian Dollar</option>
            <option value="AUD">AUD — Australian Dollar</option>
            <option value="CHF">CHF — Swiss Franc</option>
            <option value="JPY">JPY — Japanese Yen</option>
            <option value="CNY">CNY — Chinese Yuan</option>
            <option value="AED">AED — UAE Dirham</option>
            <option value="SAR">SAR — Saudi Riyal</option>
          </select>

          <label htmlFor="register-password">Password</label>
          <div className="auth-password-field">
            <input
              id="register-password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              placeholder="At least 12 characters"
              value={form.password}
              onChange={updateField}
              minLength={12}
              maxLength={128}
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

          <label htmlFor="confirm-password">Confirm password</label>
          <input
            id="confirm-password"
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            placeholder="Enter your password again"
            value={form.confirm_password}
            onChange={updateField}
            required
          />

          <button className="auth-submit" type="submit" disabled={submitting}>
            {submitting ? "Creating account..." : "Create account"}
            {!submitting && <ArrowRight size={18} />}
          </button>
        </form>

        <p className="auth-switch">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>

        <p className="auth-footer">
          Your personal financial workspace is private to your account.
        </p>
      </section>
    </main>
  );
}
