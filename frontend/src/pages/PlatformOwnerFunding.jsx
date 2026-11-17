import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Landmark, Users, Wallet, TrendingUp, Plus } from "lucide-react";
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

function Metric({ label, value, icon: Icon, detail }) {
  return (
    <section className="platform-owner-finance-card">
      <div className="platform-owner-finance-card-content">
        <div>
          <p className="platform-owner-finance-muted">{label}</p>
          <h3 className="platform-owner-finance-value large">{value}</h3>
          {detail && <p className="platform-owner-finance-muted small mt-1">{detail}</p>}
        </div>
        <div className="platform-owner-finance-icon"><Icon size={22} /></div>
      </div>
    </section>
  );
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

  useEffect(() => { load(); }, [load]);

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
  const capitalByCurrency = {};

  investors.forEach((investor) => {
    (investor.capital || []).forEach((capital) => {
      const code = capital.currency_code;
      if (!capitalByCurrency[code]) {
        capitalByCurrency[code] = {
          contributed: 0, withdrawn: 0, allocated: 0, available: 0,
        };
      }
      capitalByCurrency[code].contributed += capital.contributed_minor || 0;
      capitalByCurrency[code].withdrawn += capital.withdrawn_minor || 0;
      capitalByCurrency[code].allocated += capital.allocated_minor || 0;
      capitalByCurrency[code].available += capital.available_minor || 0;
    });
  });

  return (
    <div className="platform-owner-finance-page">
      <div className="platform-owner-finance-header">
        <div>
          <h1 className="platform-owner-finance-title">Funding & capital</h1>
          <p className="platform-owner-finance-subtitle">
            Platform funding sources, investor capital and outstanding allocations.
          </p>
        </div>
        <button className="platform-owner-finance-refresh" onClick={load} disabled={loading}>
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="platform-owner-finance-error">
          {error}
        </div>
      )}

      {success && (
        <div className="platform-owner-finance-success">
          {success}
        </div>
      )}

      {loading ? (
        <div className="platform-owner-finance-panel">Loading funding information...</div>
      ) : (
        <>
          <div className="platform-owner-finance-grid">
            <Metric label="Registered investors" value={investors.length}
              icon={Users} detail="First 100 investor records" />
            <Metric label="Active investors"
              value={investors.filter((item) => item.is_active).length}
              icon={TrendingUp} />
            <Metric label="Funding currencies" value={currencies.length}
              icon={Landmark} />
          </div>

          <section className="platform-owner-finance-panel">
            <div className="platform-owner-finance-account-heading">
              <div>
                <h2 className="platform-owner-finance-panel-title">Platform accounts</h2>
                <p className="platform-owner-finance-subtitle">
                  Accounts owned by PesaPilot. Investor contributions and platform
                  funding transactions are recorded against these accounts.
                </p>
              </div>
              <span className="platform-owner-finance-account-count">
                {accounts.length} active
              </span>
            </div>

            <form
              className="platform-owner-finance-account-form"
              onSubmit={handleCreateAccount}
            >
              <div className="platform-owner-finance-form-grid">
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
                  Institution (optional)
                  <input
                    name="institution_name"
                    value={accountForm.institution_name}
                    onChange={handleAccountChange}
                    placeholder="Bank or mobile money provider"
                    maxLength={150}
                  />
                </label>

                <label className="platform-owner-finance-field">
                  Account reference (optional)
                  <input
                    name="display_reference"
                    value={accountForm.display_reference}
                    onChange={handleAccountChange}
                    placeholder="Account number or reference"
                    maxLength={100}
                  />
                </label>
              </div>

              <button
                type="submit"
                className="platform-owner-finance-submit"
                disabled={savingAccount || accountCurrencies.length === 0}
              >
                <Plus size={16} />
                {savingAccount ? "Creating account..." : "Add platform account"}
              </button>
            </form>

            <div className="platform-owner-table-wrap">
              {accounts.length === 0 ? (
                <p className="platform-owner-finance-muted">
                  No active platform accounts yet. Create your first account above.
                </p>
              ) : (
                <table className="platform-owner-table">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th>Type</th>
                      <th>Currency</th>
                      <th>Institution</th>
                      <th>Reference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((account) => (
                      <tr key={account.id}>
                        <td><strong>{account.name}</strong></td>
                        <td>
                          {account.account_type === "mobile_money"
                            ? "Mobile money"
                            : account.account_type.charAt(0).toUpperCase()
                              + account.account_type.slice(1)}
                        </td>
                        <td>{account.currency_code}</td>
                        <td>{account.institution_name || "—"}</td>
                        <td>{account.display_reference || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          {currencies.length === 0 ? (
            <div className="platform-owner-finance-panel">No lending funding activity has been recorded yet.</div>
          ) : currencies.map((item) => {
            const capital = capitalByCurrency[item.currency_code] || {};
            return (
              <section key={item.currency_code} className="platform-owner-finance-section">
                <h2 className="platform-owner-finance-section-title">
                  {item.currency_code} funding
                </h2>

                <div className="platform-owner-finance-grid">
                  <Metric label="PesaPilot capital deployed"
                    value={money(item.funding_sources?.pesapilot_capital, item.currency_code)}
                    icon={Landmark} />
                  <Metric label="External investor capital deployed"
                    value={money(item.funding_sources?.external_investor, item.currency_code)}
                    icon={Users} />
                  <Metric label="Total disbursed"
                    value={money(item.disbursed_principal_minor, item.currency_code)}
                    icon={Wallet} />
                  <Metric label="Investor contributions"
                    value={money(capital.contributed, item.currency_code)}
                    icon={TrendingUp} />
                  <Metric label="Investor withdrawals"
                    value={money(capital.withdrawn, item.currency_code)}
                    icon={Wallet} />
                  <Metric label="Investor capital allocated"
                    value={money(capital.allocated, item.currency_code)}
                    icon={Landmark} />
                  <Metric label="Investor capital available"
                    value={money(capital.available, item.currency_code)}
                    icon={Wallet} />
                </div>
              </section>
            );
          })}

          <section className="platform-owner-finance-panel">
            <div>
              <h2 className="platform-owner-finance-panel-title">Investor capital positions</h2>
              <p className="platform-owner-finance-subtitle">
                Contributions, withdrawals, outstanding allocations and available capital.
              </p>
            </div>

            {investors.length === 0 ? (
              <p className="platform-owner-finance-muted">No investors registered.</p>
            ) : (
              <div className="platform-owner-table-wrap">
                <table className="platform-owner-table">
                  <thead>
                    <tr>
                      <th>Investor</th>
                      <th>Status</th>
                      <th>Currency</th>
                      <th>Contributed</th>
                      <th>Withdrawn</th>
                      <th>Allocated</th>
                      <th>Available</th>
                    </tr>
                  </thead>
                  <tbody>
                    {investors.flatMap((investor) =>
                      (investor.capital || []).map((capital) => (
                        <tr key={`${investor.id}-${capital.currency_code}`}>
                          <td>
                            <strong>{investor.name}</strong>
                            {investor.email && (
                              <div className="platform-owner-finance-muted small">{investor.email}</div>
                            )}
                          </td>
                          <td>{investor.is_active ? "Active" : "Inactive"}</td>
                          <td>{capital.currency_code}</td>
                          <td>{money(capital.contributed_minor, capital.currency_code)}</td>
                          <td>{money(capital.withdrawn_minor, capital.currency_code)}</td>
                          <td>{money(capital.allocated_minor, capital.currency_code)}</td>
                          <td>{money(capital.available_minor, capital.currency_code)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <p className="platform-owner-finance-muted small">
            Investor capital figures are based on posted contributions and withdrawals
            and outstanding loan allocations. Funding-source figures are cumulative
            recorded disbursements, not current cash balances.
          </p>
        </>
      )}
    </div>
  );
}
