import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Wallet, ArrowLeftRight, ChartNoAxesCombined, Sparkles,
  PiggyBank, HandCoins, Landmark, Receipt, Settings, Menu, X, Bell,
  CircleUserRound, ChevronDown, LogOut, PanelLeftClose, PanelLeftOpen, ReceiptText,
  Plus
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import SessionExpiryGuard from "../components/SessionExpiryGuard";
import { apiRequest, authApi } from "../api/client";

const navigation = [
  { label: "Overview", icon: LayoutDashboard, path: "/" },
  { label: "AI Manager", icon: Sparkles, path: "/ai-manager" },
  { label: "Accounts", icon: Wallet, path: "/accounts" },
  { label: "Transactions", icon: ArrowLeftRight, path: "/transactions" },
  { label: "Budgets", icon: ChartNoAxesCombined, path: "/budgets" },
  { label: "Savings", icon: PiggyBank, path: "/savings" },
  { label: "Loans", icon: HandCoins, path: "/loans" },
  { label: "PesaPilot Lending", icon: Landmark, path: "/lending" },
];

export default function DashboardLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [isPlatformOwner, setIsPlatformOwner] = useState(false);
  const [ownerCheckComplete, setOwnerCheckComplete] = useState(false);
  const [businessModalOpen, setBusinessModalOpen] = useState(false);
  const [businessSaving, setBusinessSaving] = useState(false);
  const [businessError, setBusinessError] = useState("");
  const [businessForm, setBusinessForm] = useState({
    name: "",
    business_type: "",
    country: "Kenya",
    currency: "KES",
    registration_number: "",
    business_phone: "",
    business_email: "",
    address: "",
  });
  const {
    user,
    profile,
    workspaces,
    activeWorkspace,
    setActiveWorkspace,
    reloadUser,
    logout,
    loading,
    isAuthenticated,
  } = useAuth();

  useEffect(() => {
    let active = true;

    if (loading) return undefined;

    if (!isAuthenticated) {
      setIsPlatformOwner(false);
      setOwnerCheckComplete(true);
      return undefined;
    }

    setOwnerCheckComplete(false);

    apiRequest("/platform/owner/access")
      .then((data) => {
        if (active) {
          setIsPlatformOwner(data.success === true && data.role === "owner");
        }
      })
      .catch(() => {
        if (active) setIsPlatformOwner(false);
      })
      .finally(() => {
        if (active) setOwnerCheckComplete(true);
      });

    return () => {
      active = false;
    };
  }, [loading, isAuthenticated]);
  const navigate = useNavigate();

  const fullName = [profile?.first_name, profile?.last_name]
    .filter(Boolean)
    .join(" ") || user?.email || "My account";

  function updateBusinessField(event) {
    const { name, value } = event.target;
    setBusinessForm((current) => ({ ...current, [name]: value }));
  }

  function closeBusinessModal() {
    if (businessSaving) return;
    setBusinessModalOpen(false);
    setBusinessError("");
  }

  async function handleBusinessWorkspaceSubmit(event) {
    event.preventDefault();
    if (businessSaving) return;

    setBusinessSaving(true);
    setBusinessError("");

    try {
      const result = await authApi.createBusinessWorkspace(businessForm);
      await reloadUser();
      setActiveWorkspace(result.workspace.id);
      setBusinessModalOpen(false);
      setBusinessForm({
        name: "",
        business_type: "",
        country: "Kenya",
        currency: "KES",
        registration_number: "",
        business_phone: "",
        business_email: "",
        address: "",
      });
    } catch (error) {
      setBusinessError(error.message || "Unable to create the business workspace. Please try again.");
    } finally {
      setBusinessSaving(false);
    }
  }

  async function handleLogout() {
    if (loggingOut) return;

    setLoggingOut(true);

    try {
      await logout();
      navigate("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-is-collapsed" : ""}`}>
      <SessionExpiryGuard />
      {menuOpen && (
        <button
          className="mobile-overlay"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        <div className="brand">
          <div className="brand-icon">P</div>
          <div>
            <strong>PesaPilot</strong>
            <span>PERSONAL FINANCE</span>
          </div>
          <button
            className="mobile-close"
            onClick={() => setMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={20} />
          </button>
          <button
            type="button"
            className="sidebar-collapse-button"
            onClick={() => setSidebarCollapsed((value) => !value)}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed
              ? <PanelLeftOpen size={19} />
              : <PanelLeftClose size={19} />}
          </button>
        </div>

        <div className="workspace-label">WORKSPACE</div>

        <div className="workspace-switcher">
          <div className="workspace-avatar">
            {activeWorkspace?.name?.charAt(0)?.toUpperCase() || "P"}
          </div>
          <div className="workspace-info">
            <strong>{activeWorkspace?.name || "My workspace"}</strong>
            <span>
              {activeWorkspace?.currency || profile?.preferred_currency || "KES"}
              {" · "}
              {activeWorkspace?.type === "business" ? "Business" : "Personal"}
            </span>
            <select
              className="workspace-select"
              aria-label="Switch workspace"
              value={activeWorkspace?.id || ""}
              onChange={(event) => setActiveWorkspace(event.target.value)}
              disabled={workspaces.length < 2}
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name} ({workspace.type === "business" ? "Business" : "Personal"})
                </option>
              ))}
            </select>
          </div>
          <ChevronDown size={16} />
        </div>

        <button
          type="button"
          className="workspace-create-button"
          onClick={() => {
            setBusinessError("");
            setBusinessModalOpen(true);
          }}
        >
          <Plus size={15} />
          <span>Create business workspace</span>
        </button>

        <div className="nav-label">MENU</div>

        <nav className="navigation">
          {navigation
            .filter((item) =>
              item.path !== "/lending" ||
              (ownerCheckComplete && !isPlatformOwner)
            )
            .map(({ label, icon: Icon, path }) => (
            <NavLink
              key={label}
              to={path}
              end={path === "/"}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
              onClick={() => setMenuOpen(false)}
              title={label}
            >
              <Icon size={19} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}

          <div className="nav-label bills-expenses-label">
            BILLS &amp; EXPENSES
          </div>

          <NavLink
            to="/bills"
            className={({ isActive }) =>
              `nav-link ${isActive ? "active" : ""}`
            }
            onClick={() => setMenuOpen(false)}
            title="Bills"
          >
            <Receipt size={19} strokeWidth={1.8} />
            <span>Bills</span>
          </NavLink>

          <NavLink
            to="/expenses"
            className={({ isActive }) =>
              `nav-link ${isActive ? "active" : ""}`
            }
            onClick={() => setMenuOpen(false)}
            title="Expenses"
          >
            <ReceiptText size={19} strokeWidth={1.8} />
            <span>Expenses</span>
          </NavLink>
        </nav>

        <div className="sidebar-bottom">
          <NavLink
            to="/settings"
            className="nav-link"
            onClick={() => setMenuOpen(false)}
            title="Settings"
          >
            <Settings size={19} />
            <span>Settings</span>
          </NavLink>

          <div className="sidebar-profile">
            <div className="profile-avatar">
              {fullName.charAt(0).toUpperCase()}
            </div>

            <div className="profile-info">
              <strong>{fullName}</strong>
              <span>{user?.email || "Personal account"}</span>
            </div>

            <button
              type="button"
              className="profile-logout"
              onClick={handleLogout}
              disabled={loggingOut}
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button
            className="mobile-menu"
            onClick={() => setMenuOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={22} />
          </button>

          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="breadcrumb-separator">/</span>
            <strong>Overview</strong>
          </div>

          <div className="topbar-actions">
            <button className="icon-button" aria-label="Notifications">
              <Bell size={19} />
              <span className="notification-dot" />
            </button>

            <div className="topbar-user">
              <CircleUserRound size={22} />
              <span>{fullName}</span>
            </div>
          </div>
        </header>

        <div className="page-content">
          <Outlet />
        </div>
      </main>

      {businessModalOpen && (
        <div
          className="workspace-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeBusinessModal();
          }}
        >
          <section
            className="workspace-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="business-workspace-title"
          >
            <div className="workspace-modal-header">
              <div>
                <h2 id="business-workspace-title">Create a business workspace</h2>
                <p>Set up a separate space for your business finances.</p>
              </div>
              <button
                type="button"
                className="workspace-modal-close"
                onClick={closeBusinessModal}
                disabled={businessSaving}
                aria-label="Close"
              >
                <X size={19} />
              </button>
            </div>

            <form className="workspace-modal-form" onSubmit={handleBusinessWorkspaceSubmit}>
              <label className="workspace-modal-field">
                Business name <span>*</span>
                <input name="name" value={businessForm.name} onChange={updateBusinessField} maxLength={150} required placeholder="e.g. Acme Traders" />
              </label>

              <div className="workspace-modal-grid">
                <label className="workspace-modal-field">
                  Business type <span>*</span>
                  <select name="business_type" value={businessForm.business_type} onChange={updateBusinessField} required>
                    <option value="">Select business type</option>
                    {[
                      "Retail", "Wholesale", "Professional Services", "Agriculture",
                      "Manufacturing", "Construction", "Transport", "Hospitality",
                      "Technology", "Other",
                    ].map((type) => <option key={type} value={type}>{type}</option>)}
                  </select>
                </label>
                <label className="workspace-modal-field">
                  Country <span>*</span>
                  <input name="country" value={businessForm.country} onChange={updateBusinessField} maxLength={100} required />
                </label>
              </div>

              <div className="workspace-modal-grid">
                <label className="workspace-modal-field">
                  Functional currency <span>*</span>
                  <select name="currency" value={businessForm.currency} onChange={updateBusinessField} required>
                    {[
                      "KES", "USD", "EUR", "GBP", "UGX", "TZS", "RWF", "ZAR", "NGN",
                      "GHS", "INR", "CAD", "AUD", "CHF", "JPY", "CNY", "AED", "SAR",
                    ].map((currency) => <option key={currency} value={currency}>{currency}</option>)}
                  </select>
                </label>
                <label className="workspace-modal-field">
                  Registration number
                  <input name="registration_number" value={businessForm.registration_number} onChange={updateBusinessField} maxLength={100} placeholder="Optional" />
                </label>
              </div>

              <div className="workspace-modal-grid">
                <label className="workspace-modal-field">
                  Business phone
                  <input name="business_phone" type="tel" value={businessForm.business_phone} onChange={updateBusinessField} maxLength={30} placeholder="Optional" />
                </label>
                <label className="workspace-modal-field">
                  Business email
                  <input name="business_email" type="email" value={businessForm.business_email} onChange={updateBusinessField} maxLength={255} placeholder="Optional" />
                </label>
              </div>

              <label className="workspace-modal-field">
                Business address
                <input name="address" value={businessForm.address} onChange={updateBusinessField} maxLength={255} placeholder="Optional" />
              </label>

              {businessError && <p className="workspace-modal-error" role="alert">{businessError}</p>}

              <div className="workspace-modal-actions">
                <button type="button" className="secondary-button" onClick={closeBusinessModal} disabled={businessSaving}>Cancel</button>
                <button type="submit" className="primary-button" disabled={businessSaving}>
                  {businessSaving ? "Creating..." : "Create workspace"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
