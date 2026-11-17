import { useEffect, useState } from "react";
import { RefreshCw, BriefcaseBusiness, Wallet, TrendingUp, AlertTriangle } from "lucide-react";
import { platformOwnerApi } from "../api/client";

function formatMoney(minor, currency) {
  const amount = Number(minor || 0) / 100;
  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency: currency || "KES",
    maximumFractionDigits: 2,
  }).format(amount);
}

function MetricCard({ label, value, icon: Icon, detail }) {
  return (
    <section className="platform-owner-finance-card platform-owner-overview-metric">
      <div className="platform-owner-finance-card-content">
        <div>
          <p className="platform-owner-finance-muted">{label}</p>
          <h3 className="platform-owner-finance-value large">{value}</h3>
          {detail && <p className="platform-owner-finance-muted small">{detail}</p>}
        </div>
        <div className="platform-owner-finance-icon">
          <Icon size={22} />
        </div>
      </div>
    </section>
  );
}

export default function PlatformOwnerOverview() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadReport() {
    setLoading(true);
    setError("");

    try {
      const response = await platformOwnerApi.getPortfolioReport();
      setReport(response);
    } catch (err) {
      setError(err.message || "Could not load the lending portfolio report.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadReport();
  }, []);

  const currencies = report?.currencies || [];

  return (
    <div className="platform-owner-page platform-owner-overview-page">
      <div className="platform-owner-page-header">
        <div>
          <h1 className="platform-owner-page-title">Platform overview</h1>
          <p className="platform-owner-page-subtitle">
            PesaPilot Lending Business portfolio and performance.
          </p>
        </div>

        <button
          type="button"
          className="platform-owner-button secondary"
          onClick={loadReport}
          disabled={loading}
        >
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="platform-owner-notice error">
          {error}
        </div>
      )}

      {loading ? (
        <div className="platform-owner-finance-panel">Loading portfolio report...</div>
      ) : currencies.length === 0 ? (
        <div className="platform-owner-finance-panel">
          <h2 className="font-semibold">No portfolio activity yet</h2>
          <p className="text-sm text-muted-foreground mt-2">
            Portfolio figures will appear here when lending activity is recorded.
          </p>
        </div>
      ) : (
        currencies.map((item) => (
          <section key={item.currency_code} className="platform-owner-finance-section">
            <h2 className="platform-owner-finance-section-title">{item.currency_code} portfolio</h2>

            <div className="platform-owner-finance-grid">
              <MetricCard
                label="Total loans"
                value={item.loan_count}
                icon={BriefcaseBusiness}
                detail={`${item.overdue_loan_count || 0} overdue loans`}
              />
              <MetricCard
                label="Disbursed principal"
                value={formatMoney(item.disbursed_principal_minor, item.currency_code)}
                icon={Wallet}
              />
              <MetricCard
                label="Outstanding principal"
                value={formatMoney(item.outstanding_principal_minor, item.currency_code)}
                icon={TrendingUp}
              />
              <MetricCard
                label="Principal repaid"
                value={formatMoney(item.confirmed_principal_repaid_minor, item.currency_code)}
                icon={TrendingUp}
              />
              <MetricCard
                label="Interest collected"
                value={formatMoney(item.interest_collected_minor, item.currency_code)}
                icon={Wallet}
              />
              <MetricCard
                label="Pending repayments"
                value={formatMoney(item.pending_repayments_minor, item.currency_code)}
                icon={Wallet}
              />
              <MetricCard
                label="Overdue installments"
                value={item.overdue_installment_count || 0}
                icon={AlertTriangle}
              />
              <MetricCard
                label="Approved principal"
                value={formatMoney(item.approved_principal_minor, item.currency_code)}
                icon={BriefcaseBusiness}
              />
            </div>

            <div className="platform-owner-finance-panel">
              <h3 className="platform-owner-finance-panel-title">Funding sources</h3>
              <div className="platform-owner-finance-grid two-columns">
                <div>
                  <p className="platform-owner-finance-muted">PesaPilot capital</p>
                  <p className="platform-owner-finance-value">
                    {formatMoney(item.funding_sources?.pesapilot_capital, item.currency_code)}
                  </p>
                </div>
                <div>
                  <p className="platform-owner-finance-muted">External investor capital</p>
                  <p className="platform-owner-finance-value">
                    {formatMoney(item.funding_sources?.external_investor, item.currency_code)}
                  </p>
                </div>
              </div>
            </div>
          </section>
        ))
      )}

      {report?.as_of_date && (
        <p className="platform-owner-finance-muted small">
          Report as of {report.as_of_date}
        </p>
      )}
    </div>
  );
}
