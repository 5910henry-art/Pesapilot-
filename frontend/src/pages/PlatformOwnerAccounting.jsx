import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Wallet, TrendingUp, Landmark, AlertTriangle } from "lucide-react";
import { platformOwnerApi } from "../api/client";

const ZERO_DECIMAL = new Set([
  "JPY", "UGX", "RWF", "BIF", "DJF", "GNF", "KMF", "XAF", "XOF",
]);

function money(minor, currency = "KES") {
  const digits = ZERO_DECIMAL.has(currency) ? 0 : 2;
  return new Intl.NumberFormat("en", {
    style: "currency",
    currency,
    maximumFractionDigits: digits,
  }).format(Number(minor || 0) / (10 ** digits));
}

function Metric({ label, value, icon: Icon }) {
  return (
    <section className="card">
      <div className="platform-owner-finance-card-content">
        <div>
          <p className="platform-owner-finance-muted">{label}</p>
          <h3 className="platform-owner-finance-value large">{value}</h3>
        </div>
        <div className="platform-owner-finance-icon"><Icon size={22} /></div>
      </div>
    </section>
  );
}

export default function PlatformOwnerAccounting() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await platformOwnerApi.getPortfolioReport());
    } catch (err) {
      setError(err.message || "Could not load the financial summary.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const currencies = report?.currencies || [];

  return (
    <div className="platform-owner-finance-page">
      <div className="platform-owner-finance-header">
        <div>
          <h1 className="platform-owner-finance-title">Accounting overview</h1>
          <p className="platform-owner-finance-subtitle">
            Lending portfolio financial movements grouped by currency.
          </p>
        </div>
        <button className="platform-owner-finance-refresh" onClick={load} disabled={loading}>
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="platform-owner-notice">
        <Landmark size={18} />
        <span>
          This is a portfolio financial summary, not a general ledger or trial
          balance. It reports lending totals and does not display individual
          journal lines or debit and credit balances.
        </span>
      </div>

      {error && (
        <div className="platform-owner-finance-error">
          {error}
        </div>
      )}

      {loading ? (
        <div className="platform-owner-finance-panel">Loading financial summary...</div>
      ) : currencies.length === 0 ? (
        <div className="platform-owner-finance-panel">No lending financial activity is available yet.</div>
      ) : (
        currencies.map((item) => (
          <section key={item.currency_code} className="platform-owner-finance-section">
            <h2 className="platform-owner-finance-section-title">
              {item.currency_code} financial summary
            </h2>

            <div className="platform-owner-finance-grid">
              <Metric label="Approved principal"
                value={money(item.approved_principal_minor, item.currency_code)}
                icon={Landmark} />
              <Metric label="Disbursed principal"
                value={money(item.disbursed_principal_minor, item.currency_code)}
                icon={Wallet} />
              <Metric label="Outstanding principal"
                value={money(item.outstanding_principal_minor, item.currency_code)}
                icon={TrendingUp} />
              <Metric label="Confirmed repayments"
                value={money(item.confirmed_repayments_minor, item.currency_code)}
                icon={Wallet} />
              <Metric label="Principal recovered"
                value={money(item.confirmed_principal_repaid_minor, item.currency_code)}
                icon={TrendingUp} />
              <Metric label="Interest collected"
                value={money(item.interest_collected_minor, item.currency_code)}
                icon={Landmark} />
              <Metric label="Pending repayments"
                value={money(item.pending_repayments_minor, item.currency_code)}
                icon={Wallet} />
              <Metric label="Reversed repayments"
                value={money(item.reversed_repayments_minor, item.currency_code)}
                icon={AlertTriangle} />
              <Metric label="Failed repayments"
                value={money(item.failed_repayments_minor, item.currency_code)}
                icon={AlertTriangle} />
            </div>

            <section className="platform-owner-finance-panel">
              <h3 className="platform-owner-finance-panel-title">Funding source breakdown</h3>
              <div className="platform-owner-finance-grid two-columns">
                <div>
                  <p className="platform-owner-finance-muted">PesaPilot capital</p>
                  <p className="platform-owner-finance-value">
                    {money(item.funding_sources?.pesapilot_capital, item.currency_code)}
                  </p>
                </div>
                <div>
                  <p className="platform-owner-finance-muted">External investor capital</p>
                  <p className="platform-owner-finance-value">
                    {money(item.funding_sources?.external_investor, item.currency_code)}
                  </p>
                </div>
              </div>
            </section>

            <section className="platform-owner-finance-panel">
              <h3 className="font-semibold mb-3">Portfolio activity</h3>
              <div className="platform-owner-finance-grid three-columns">
                <div>
                  <p className="text-muted-foreground">Total loans</p>
                  <p className="font-semibold text-lg">{item.loan_count}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Overdue loans</p>
                  <p className="font-semibold text-lg">{item.overdue_loan_count || 0}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Overdue installments</p>
                  <p className="font-semibold text-lg">{item.overdue_installment_count || 0}</p>
                </div>
              </div>
            </section>
          </section>
        ))
      )}

      {report?.as_of_date && (
        <p className="platform-owner-finance-muted small">
          Report as of {report.as_of_date}.
        </p>
      )}
    </div>
  );
}
