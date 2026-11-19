import { useEffect, useState } from "react";
import {
  RefreshCw,
  BriefcaseBusiness,
  Wallet,
  TrendingUp,
  AlertTriangle,
  Landmark,
  Banknote,
  CircleDollarSign,
  Clock3,
  ShieldCheck,
  Activity,
  ArrowUpRight,
} from "lucide-react";
import { platformOwnerApi } from "../api/client";

function formatMoney(minor, currency = "KES") {
  const amount = Number(minor || 0) / 100;

  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency: currency || "KES",
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatPercent(value) {
  return `${Math.max(0, Math.min(100, value)).toFixed(1)}%`;
}

function MetricCard({ label, value, icon: Icon, detail, tone = "green" }) {
  return (
    <article className={`po-overview-card tone-${tone}`}>
      <div className="po-overview-card-top">
        <span className="po-overview-card-label">{label}</span>
        <span className="po-overview-card-icon">
          <Icon size={17} strokeWidth={2} />
        </span>
      </div>

      <strong className="po-overview-card-value">{value}</strong>

      {detail && (
        <span className="po-overview-card-detail">{detail}</span>
      )}
    </article>
  );
}

function FundingCard({ title, amount, percentage, icon: Icon, tone }) {
  return (
    <div className={`po-funding-card ${tone}`}>
      <div className="po-funding-card-heading">
        <span className="po-funding-icon">
          <Icon size={17} />
        </span>
        <span>{title}</span>
      </div>

      <strong>{amount}</strong>

      <div className="po-funding-track">
        <span style={{ width: `${percentage}%` }} />
      </div>

      <small>{formatPercent(percentage)} of recorded funding</small>
    </div>
  );
}

function PortfolioSection({ item }) {
  const currency = item.currency_code || "KES";
  const disbursed = Number(item.disbursed_principal_minor || 0);
  const repaid = Number(item.confirmed_principal_repaid_minor || 0);
  const outstanding = Number(item.outstanding_principal_minor || 0);
  const capital = Number(item.funding_sources?.pesapilot_capital || 0);
  const investor = Number(item.funding_sources?.external_investor || 0);
  const totalFunding = capital + investor;

  const recoveryRate =
    disbursed > 0 ? Math.min(100, Math.max(0, (repaid / disbursed) * 100)) : 0;

  const capitalShare =
    totalFunding > 0 ? (capital / totalFunding) * 100 : 0;

  const investorShare =
    totalFunding > 0 ? (investor / totalFunding) * 100 : 0;

  const overdueLoans = Number(item.overdue_loan_count || 0);
  const overdueInstallments = Number(item.overdue_installment_count || 0);

  return (
    <section className="po-overview-currency-section">
      <div className="po-overview-section-heading">
        <div>
          <span className="po-overview-eyebrow">PORTFOLIO BREAKDOWN</span>
          <h2>{currency} portfolio</h2>
        </div>

        <span className="po-overview-currency-badge">
          <CircleDollarSign size={14} />
          {currency}
        </span>
      </div>

      <div className="po-overview-metrics">
        <MetricCard
          label="Total loans"
          value={Number(item.loan_count || 0).toLocaleString("en-KE")}
          detail={`${overdueLoans} overdue loans`}
          icon={BriefcaseBusiness}
          tone="green"
        />

        <MetricCard
          label="Disbursed principal"
          value={formatMoney(disbursed, currency)}
          detail="Total principal issued"
          icon={Banknote}
          tone="blue"
        />

        <MetricCard
          label="Outstanding principal"
          value={formatMoney(outstanding, currency)}
          detail="Principal still unpaid"
          icon={Wallet}
          tone="purple"
        />

        <MetricCard
          label="Principal repaid"
          value={formatMoney(repaid, currency)}
          detail="Confirmed repayments"
          icon={TrendingUp}
          tone="green"
        />

        <MetricCard
          label="Interest collected"
          value={formatMoney(item.interest_collected_minor, currency)}
          detail="Interest received"
          icon={CircleDollarSign}
          tone="amber"
        />

        <MetricCard
          label="Pending repayments"
          value={formatMoney(item.pending_repayments_minor, currency)}
          detail="Awaiting confirmation"
          icon={Clock3}
          tone="blue"
        />

        <MetricCard
          label="Overdue installments"
          value={overdueInstallments.toLocaleString("en-KE")}
          detail="Installments past due"
          icon={AlertTriangle}
          tone="red"
        />

        <MetricCard
          label="Approved principal"
          value={formatMoney(item.approved_principal_minor, currency)}
          detail="Approved loan value"
          icon={ShieldCheck}
          tone="purple"
        />
      </div>

      <div className="po-overview-insight-grid">
        <section className="po-overview-panel po-recovery-panel">
          <div className="po-overview-panel-heading">
            <div>
              <span className="po-overview-eyebrow">REPAYMENT PERFORMANCE</span>
              <h3>Principal recovery</h3>
            </div>
            <span className="po-overview-panel-icon">
              <Activity size={18} />
            </span>
          </div>

          <div className="po-recovery-value-row">
            <strong>{formatPercent(recoveryRate)}</strong>
            <span>of disbursed principal repaid</span>
          </div>

          <div className="po-recovery-track">
            <span style={{ width: `${recoveryRate}%` }} />
          </div>

          <div className="po-recovery-breakdown">
            <div>
              <span className="po-dot green" />
              <span>Repaid</span>
              <strong>{formatMoney(repaid, currency)}</strong>
            </div>
            <div>
              <span className="po-dot blue" />
              <span>Disbursed</span>
              <strong>{formatMoney(disbursed, currency)}</strong>
            </div>
          </div>
        </section>

        <section className="po-overview-panel po-funding-panel">
          <div className="po-overview-panel-heading">
            <div>
              <span className="po-overview-eyebrow">CAPITAL STRUCTURE</span>
              <h3>Funding sources</h3>
            </div>
            <span className="po-overview-panel-icon">
              <Landmark size={18} />
            </span>
          </div>

          <div className="po-funding-total">
            <span>Total recorded funding</span>
            <strong>{formatMoney(totalFunding, currency)}</strong>
          </div>

          <div className="po-funding-cards">
            <FundingCard
              title="PesaPilot capital"
              amount={formatMoney(capital, currency)}
              percentage={capitalShare}
              icon={Wallet}
              tone="capital"
            />

            <FundingCard
              title="Investor capital"
              amount={formatMoney(investor, currency)}
              percentage={investorShare}
              icon={Landmark}
              tone="investor"
            />
          </div>
        </section>
      </div>
    </section>
  );
}

export default function PlatformOwnerOverview() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function loadReport(isRefresh = false) {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    setError("");

    try {
      const response = await platformOwnerApi.getPortfolioReport();
      setReport(response);
    } catch (err) {
      setError(err.message || "Could not load the lending portfolio report.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    loadReport();
  }, []);

  const currencies = report?.currencies || [];

  return (
    <main className="platform-owner-page platform-owner-overview-page po-modern-overview">
      <header className="po-overview-hero">
        <div className="po-overview-hero-copy">
          <span className="po-overview-eyebrow">PESAPILOT • LENDING MANAGEMENT</span>
          <h1>Portfolio overview</h1>
          <p>
            Monitor lending performance, capital distribution and repayment activity.
          </p>
        </div>

        <button
          type="button"
          className="po-overview-refresh"
          onClick={() => loadReport(true)}
          disabled={loading || refreshing}
        >
          <RefreshCw
            size={15}
            className={refreshing ? "animate-spin" : ""}
          />
          {refreshing ? "Refreshing..." : "Refresh data"}
        </button>
      </header>

      {error && (
        <div className="po-overview-error">
          <AlertTriangle size={17} />
          <span>{error}</span>
          <button type="button" onClick={() => loadReport(true)}>
            Try again
          </button>
        </div>
      )}

      {loading ? (
        <div className="po-overview-loading">
          <div className="po-overview-loading-icon">
            <RefreshCw size={22} className="animate-spin" />
          </div>
          <strong>Loading portfolio</strong>
          <span>Preparing your lending performance figures...</span>
          <div className="po-overview-skeleton-grid">
            {Array.from({ length: 8 }).map((_, index) => (
              <div className="po-overview-skeleton" key={index} />
            ))}
          </div>
        </div>
      ) : currencies.length === 0 ? (
        <div className="po-overview-empty">
          <span className="po-overview-empty-icon">
            <BriefcaseBusiness size={25} />
          </span>
          <h2>No portfolio activity yet</h2>
          <p>
            Your portfolio figures will appear here once lending activity is recorded.
          </p>
          <button
            type="button"
            className="po-overview-refresh"
            onClick={() => loadReport(true)}
            disabled={refreshing}
          >
            <RefreshCw size={15} />
            Refresh report
          </button>
        </div>
      ) : (
        <>
          {currencies.map((item) => (
            <PortfolioSection key={item.currency_code} item={item} />
          ))}

          <footer className="po-overview-footer">
            <span>
              <span className="po-live-dot" />
              Portfolio report loaded
            </span>
            <span>
              {report?.as_of_date
                ? `Report date: ${report.as_of_date}`
                : "Latest available report"}
            </span>
            <span className="po-overview-footer-brand">
              PesaPilot <ArrowUpRight size={13} />
            </span>
          </footer>
        </>
      )}
    </main>
  );
}
