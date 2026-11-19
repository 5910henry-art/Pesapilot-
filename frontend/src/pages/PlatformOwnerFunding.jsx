import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  Landmark,
  Users,
  Wallet,
  TrendingUp,
  Plus,
  Search,
  Building2,
  Smartphone,
  Banknote,
  ArrowDownToLine,
  ArrowUpFromLine,
  CircleDollarSign,
} from "lucide-react";
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

function Metric({ label, value, icon: Icon, detail, tone = "" }) {
  return (
    <article className={`funding-stat-card ${tone}`}>
      <div className="funding-stat-top">
        <span className="funding-stat-label">{label}</span>
        <span className="funding-stat-icon">
          <Icon size={17} strokeWidth={2} />
        </span>
      </div>
      <strong className="funding-stat-value">{value}</strong>
      {detail && <span className="funding-stat-detail">{detail}</span>}
    </article>
  );
}

function AccountIcon({ type }) {
  if (type === "mobile_money") return <Smartphone size={19} />;
  if (type === "cash") return <Banknote size={19} />;
  return <Building2 size={19} />;
}

function accountTypeLabel(type) {
  if (type === "mobile_money") return "Mobile money";
  if (type === "cash") return "Cash";
  return "Bank";
}

export default function PlatformOwnerFunding() {
  const [report, setReport] = useState(null);
  const [investors, setInvestors] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [accountCurrencies, setAccountCurrencies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingAccount, setSavingAccount] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [investorSearch, setInvestorSearch] = useState("");
  const [accountForm, setAccountForm] = useState({
    name: "PesaPilot Main Account",
    account_type: "bank",
    currency_code: "KES",
    institution_name: "",
    display_reference: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [portfolio, investorResponse, accountResponse] = await Promise.all([
        platformOwnerApi.getPortfolioReport(),
        platformOwnerApi.listInvestors({ page: 1, per_page: 100 }),
        platformOwnerApi.listFinancialAccounts(),
      ]);
      setReport(portfolio);
      setInvestors(investorResponse.investors || []);
      setAccounts(accountResponse.accounts || []);
      setAccountCurrencies(accountResponse.currencies || []);
    } catch (err) {
      setError(err.message || "Could not load funding information.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleAccountChange = (event) => {
    const { name, value } = event.target;
    setAccountForm((current) => ({ ...current, [name]: value }));
  };

  const handleCreateAccount = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!accountForm.name.trim() || !accountForm.currency_code) {
      setError("Enter an account name and select a currency.");
      return;
    }

    setSavingAccount(true);
    try {
      const response = await platformOwnerApi.createFinancialAccount({
        ...accountForm,
        name: accountForm.name.trim(),
        institution_name: accountForm.institution_name.trim(),
        display_reference: accountForm.display_reference.trim(),
      });

      setSuccess(response.message || "Platform account created successfully.");
      setAccountForm((current) => ({
        ...current,
        name: "",
        institution_name: "",
        display_reference: "",
      }));
      await load();
    } catch (err) {
      setError(err.message || "Could not create the platform account.");
    } finally {
      setSavingAccount(false);
    }
  };

  const currencies = report?.currencies || [];

  const capitalByCurrency = useMemo(() => {
    const totals = {};

    investors.forEach((investor) => {
      (investor.capital || []).forEach((capital) => {
        const code = capital.currency_code;
        if (!totals[code]) {
          totals[code] = {
            contributed: 0,
            withdrawn: 0,
            allocated: 0,
            available: 0,
          };
        }
        totals[code].contributed += capital.contributed_minor || 0;
        totals[code].withdrawn += capital.withdrawn_minor || 0;
        totals[code].allocated += capital.allocated_minor || 0;
        totals[code].available += capital.available_minor || 0;
      });
    });

    return totals;
  }, [investors]);

  const investorRows = useMemo(() => {
    const query = investorSearch.trim().toLowerCase();

    return investors.flatMap((investor) =>
      (investor.capital || []).map((capital) => ({
        investor,
        capital,
      }))
    ).filter(({ investor, capital }) => {
      if (!query) return true;
      return [
        investor.name,
        investor.email,
        capital.currency_code,
        investor.is_active ? "active" : "inactive",
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [investors, investorSearch]);

  const activeInvestors = investors.filter((item) => item.is_active).length;

  return (
    <div className="platform-owner-finance-page funding-page">
      <header className="platform-owner-finance-header funding-header">
        <div>
          <span className="funding-eyebrow">FINANCIAL MANAGEMENT</span>
          <h1 className="platform-owner-finance-title">Funding &amp; capital</h1>
          <p className="platform-owner-finance-subtitle">
            Manage platform accounts, funding sources and investor capital.
          </p>
        </div>

        <button
          className="platform-owner-finance-refresh"
          onClick={load}
          disabled={loading}
          type="button"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          {loading ? "Refreshing..." : "Refresh data"}
        </button>
      </header>

      {error && <div className="platform-owner-finance-error">{error}</div>}
      {success && <div className="platform-owner-finance-success">{success}</div>}

      {loading ? (
        <div className="platform-owner-finance-panel funding-loading">
          <RefreshCw size={20} className="animate-spin" />
          <span>Loading funding information...</span>
        </div>
      ) : (
        <>
          <section className="funding-overview">
            <div className="funding-section-heading">
              <div>
                <h2>Capital overview</h2>
                <p>A quick summary of your investor network.</p>
              </div>
              <span className="funding-section-tag">OVERVIEW</span>
            </div>

            <div className="funding-stat-grid">
              <Metric
                label="Registered investors"
                value={investors.length}
                icon={Users}
                detail="First 100 investor records"
                tone="green"
              />
              <Metric
                label="Active investors"
                value={activeInvestors}
                icon={TrendingUp}
                detail={`${investors.length - activeInvestors} inactive`}
                tone="blue"
              />
              <Metric
                label="Funding currencies"
                value={currencies.length}
                icon={CircleDollarSign}
                detail="Currencies with lending activity"
                tone="purple"
              />
              <Metric
                label="Platform accounts"
                value={accounts.length}
                icon={Landmark}
                detail="Registered funding accounts"
                tone="amber"
              />
            </div>
          </section>

          <section className="platform-owner-finance-panel funding-panel">
            <div className="funding-panel-heading">
              <div className="funding-heading-icon">
                <Landmark size={19} />
              </div>
              <div>
                <h2 className="platform-owner-finance-panel-title">Platform accounts</h2>
                <p className="platform-owner-finance-subtitle">
                  Accounts owned by PesaPilot for recording funding transactions.
                </p>
              </div>
              <span className="platform-owner-finance-account-count">
                {accounts.length} accounts
              </span>
            </div>

            <form
              className="platform-owner-finance-account-form funding-account-form"
              onSubmit={handleCreateAccount}
            >
              <div className="funding-form-heading">
                <div>
                  <h3>Add a platform account</h3>
                  <p>Register a bank, mobile money or cash account.</p>
                </div>
                <span className="funding-form-plus"><Plus size={17} /></span>
              </div>

              <div className="platform-owner-finance-form-grid funding-form-grid">
                <label className="platform-owner-finance-field">
                  Account name
                  <input
                    name="name"
                    value={accountForm.name}
                    onChange={handleAccountChange}
                    placeholder="PesaPilot Main Account"
                    maxLength={150}
                    required
                  />
                </label>

                <label className="platform-owner-finance-field">
                  Account type
                  <select
                    name="account_type"
                    value={accountForm.account_type}
                    onChange={handleAccountChange}
                    required
                  >
                    <option value="bank">Bank</option>
                    <option value="mobile_money">Mobile money</option>
                    <option value="cash">Cash</option>
                  </select>
                </label>

                <label className="platform-owner-finance-field">
                  Currency
                  <select
                    name="currency_code"
                    value={accountForm.currency_code}
                    onChange={handleAccountChange}
                    required
                  >
                    {accountCurrencies.map((currency) => (
                      <option key={currency.code} value={currency.code}>
                        {currency.code} — {currency.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="platform-owner-finance-field">
                  Institution <span className="funding-optional">Optional</span>
                  <input
                    name="institution_name"
                    value={accountForm.institution_name}
                    onChange={handleAccountChange}
                    placeholder="Bank or mobile money provider"
                    maxLength={150}
                  />
                </label>

                <label className="platform-owner-finance-field">
                  Account reference <span className="funding-optional">Optional</span>
                  <input
                    name="display_reference"
                    value={accountForm.display_reference}
                    onChange={handleAccountChange}
                    placeholder="Account number or reference"
                    maxLength={100}
                  />
                </label>
              </div>

              <div className="funding-form-actions">
                <span>Account details are used for transaction records.</span>
                <button
                  type="submit"
                  className="platform-owner-finance-submit"
                  disabled={savingAccount || accountCurrencies.length === 0}
                >
                  {savingAccount ? (
                    <RefreshCw size={15} className="animate-spin" />
                  ) : (
                    <Plus size={16} />
                  )}
                  {savingAccount ? "Creating account..." : "Add platform account"}
                </button>
              </div>
            </form>

            {accounts.length === 0 ? (
              <div className="funding-empty">
                <Landmark size={24} />
                <strong>No platform accounts yet</strong>
                <span>Create your first account using the form above.</span>
              </div>
            ) : (
              <div className="funding-account-grid">
                {accounts.map((account) => (
                  <article className="funding-account-card" key={account.id}>
                    <div className="funding-account-card-top">
                      <span className="funding-account-icon">
                        <AccountIcon type={account.account_type} />
                      </span>
                      <span className="funding-account-type">
                        {accountTypeLabel(account.account_type)}
                      </span>
                    </div>
                    <h3>{account.name}</h3>
                    <div className="funding-account-currency">
                      {account.currency_code}
                    </div>
                    <div className="funding-account-meta">
                      <span>Institution</span>
                      <strong>{account.institution_name || "Not provided"}</strong>
                    </div>
                    <div className="funding-account-meta">
                      <span>Reference</span>
                      <strong>{account.display_reference || "Not provided"}</strong>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="funding-currency-section">
            <div className="funding-section-heading">
              <div>
                <h2>Funding by currency</h2>
                <p>Capital deployment and investor balances, grouped by currency.</p>
              </div>
            </div>

            {currencies.length === 0 ? (
              <div className="funding-empty">
                <Wallet size={24} />
                <strong>No lending funding activity</strong>
                <span>Funding summaries will appear when activity is recorded.</span>
              </div>
            ) : (
              <div className="funding-currency-list">
                {currencies.map((item) => {
                  const code = item.currency_code;
                  const capital = capitalByCurrency[code] || {};

                  return (
                    <article className="funding-currency-card" key={code}>
                      <div className="funding-currency-heading">
                        <div className="funding-currency-symbol">
                          <CircleDollarSign size={19} />
                        </div>
                        <div>
                          <h3>{code} funding</h3>
                          <span>Capital position summary</span>
                        </div>
                      </div>

                      <div className="funding-currency-primary">
                        <div>
                          <span>PesaPilot capital deployed</span>
                          <strong>{money(item.funding_sources?.pesapilot_capital, code)}</strong>
                        </div>
                        <div>
                          <span>External investor capital</span>
                          <strong>{money(item.funding_sources?.external_investor, code)}</strong>
                        </div>
                        <div className="funding-currency-total">
                          <span>Total disbursed</span>
                          <strong>{money(item.disbursed_principal_minor, code)}</strong>
                        </div>
                      </div>

                      <div className="funding-capital-breakdown">
                        <div>
                          <span><ArrowDownToLine size={14} /> Contributions</span>
                          <strong>{money(capital.contributed, code)}</strong>
                        </div>
                        <div>
                          <span><ArrowUpFromLine size={14} /> Withdrawals</span>
                          <strong>{money(capital.withdrawn, code)}</strong>
                        </div>
                        <div>
                          <span><Landmark size={14} /> Allocated</span>
                          <strong>{money(capital.allocated, code)}</strong>
                        </div>
                        <div>
                          <span><Wallet size={14} /> Available</span>
                          <strong>{money(capital.available, code)}</strong>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </section>

          <section className="platform-owner-finance-panel funding-panel">
            <div className="funding-investor-heading">
              <div>
                <div className="funding-panel-heading">
                  <div className="funding-heading-icon">
                    <Users size={19} />
                  </div>
                  <div>
                    <h2 className="platform-owner-finance-panel-title">
                      Investor capital positions
                    </h2>
                    <p className="platform-owner-finance-subtitle">
                      Contributions, withdrawals, allocations and available capital.
                    </p>
                  </div>
                </div>
              </div>
              <span className="platform-owner-finance-account-count">
                {investorRows.length} positions
              </span>
            </div>

            <div className="funding-investor-toolbar">
              <label className="funding-search">
                <Search size={16} />
                <input
                  value={investorSearch}
                  onChange={(event) => setInvestorSearch(event.target.value)}
                  placeholder="Search investor, email or currency..."
                />
              </label>
              <span>{investors.length} investors loaded</span>
            </div>

            {investorRows.length === 0 ? (
              <div className="funding-empty">
                <Users size={24} />
                <strong>{investors.length ? "No matching positions" : "No investors registered"}</strong>
                <span>
                  {investors.length
                    ? "Try another investor name, email or currency."
                    : "Registered investor capital positions will appear here."}
                </span>
              </div>
            ) : (
              <div className="funding-investor-grid">
                {investorRows.map(({ investor, capital }) => (
                  <article
                    className="funding-investor-card"
                    key={`${investor.id}-${capital.currency_code}`}
                  >
                    <div className="funding-investor-card-heading">
                      <div className="funding-investor-avatar">
                        {(investor.name || "I").trim().charAt(0).toUpperCase()}
                      </div>
                      <div className="funding-investor-identity">
                        <strong>{investor.name}</strong>
                        <span>{investor.email || "No email provided"}</span>
                      </div>
                      <span className={`funding-investor-status ${investor.is_active ? "active" : "inactive"}`}>
                        {investor.is_active ? "Active" : "Inactive"}
                      </span>
                    </div>

                    <div className="funding-investor-currency">
                      {capital.currency_code}
                    </div>

                    <div className="funding-investor-values">
                      <div>
                        <span>Contributed</span>
                        <strong>{money(capital.contributed_minor, capital.currency_code)}</strong>
                      </div>
                      <div>
                        <span>Withdrawn</span>
                        <strong>{money(capital.withdrawn_minor, capital.currency_code)}</strong>
                      </div>
                      <div>
                        <span>Allocated</span>
                        <strong>{money(capital.allocated_minor, capital.currency_code)}</strong>
                      </div>
                      <div className="available">
                        <span>Available</span>
                        <strong>{money(capital.available_minor, capital.currency_code)}</strong>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <p className="funding-footnote">
            Investor capital figures are based on posted contributions and withdrawals
            and outstanding loan allocations. Funding-source figures are cumulative
            recorded disbursements, not current cash balances.
          </p>
        </>
      )}
    </div>
  );
}
