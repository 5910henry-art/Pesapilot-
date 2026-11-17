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
    <section className="card">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <h3 className="text-2xl font-semibold mt-2">{value}</h3>
          {detail && <p className="text-xs text-muted-foreground mt-1">{detail}</p>}
        </div>
        <div className="rounded-xl p-3 bg-muted">
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
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">Platform overview</h1>
          <p className="text-sm text-muted-foreground mt-1">
            PesaPilot Lending Business portfolio and performance.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-secondary"
          onClick={loadReport}
          disabled={loading}
        >
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-300 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="card p-6">Loading portfolio report...</div>
      ) : currencies.length === 0 ? (
        <div className="card p-6">
          <h2 className="font-semibold">No portfolio activity yet</h2>
          <p className="text-sm text-muted-foreground mt-2">
            Portfolio figures will appear here when lending activity is recorded.
          </p>
        </div>
      ) : (
        currencies.map((item) => (
          <section key={item.currency_code} className="space-y-4">
            <h2 className="text-lg font-semibold">{item.currency_code} portfolio</h2>

            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
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

            <div className="card p-4">
              <h3 className="font-semibold mb-3">Funding sources</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">PesaPilot capital</p>
                  <p className="text-lg font-semibold mt-1">
                    {formatMoney(item.funding_sources?.pesapilot_capital, item.currency_code)}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">External investor capital</p>
                  <p className="text-lg font-semibold mt-1">
                    {formatMoney(item.funding_sources?.external_investor, item.currency_code)}
                  </p>
                </div>
              </div>
            </div>
          </section>
        ))
      )}

      {report?.as_of_date && (
        <p className="text-xs text-muted-foreground">
          Report as of {report.as_of_date}
        </p>
      )}
    </div>
  );
}
