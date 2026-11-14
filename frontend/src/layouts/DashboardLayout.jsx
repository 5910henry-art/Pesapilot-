import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Wallet, ArrowLeftRight, ChartNoAxesCombined,
  PiggyBank, HandCoins, Receipt, Settings, Menu, X, Bell,
  CircleUserRound, ChevronDown, LogOut
} from "lucide-react";
import { useAuth } from "../context/useAuth";

const navigation = [
  { label: "Overview", icon: LayoutDashboard, path: "/" },
  { label: "Accounts", icon: Wallet, path: "/accounts" },
  { label: "Transactions", icon: ArrowLeftRight, path: "/transactions" },
  { label: "Budgets", icon: ChartNoAxesCombined, path: "/budgets" },
  { label: "Savings", icon: PiggyBank, path: "/savings" },
  { label: "Loans", icon: HandCoins, path: "/loans" },
  { label: "Bills", icon: Receipt, path: "/bills" },
];

export default function DashboardLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const { user, profile, activeWorkspace, logout } = useAuth();
  const navigate = useNavigate();

  const fullName = [profile?.first_name, profile?.last_name]
    .filter(Boolean)
    .join(" ") || user?.email || "My account";

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
    <div className="app-shell">
      {menuOpen && (
        <button
          className="mobile-overlay"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
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
              {activeWorkspace?.type || "Personal"}
            </span>
          </div>
          <ChevronDown size={16} />
        </div>

        <div className="nav-label">MENU</div>

        <nav className="navigation">
          {navigation.map(({ label, icon: Icon, path }) => (
            <NavLink
              key={label}
              to={path}
              end={path === "/"}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
              onClick={() => setMenuOpen(false)}
            >
              <Icon size={19} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <NavLink
            to="/settings"
            className="nav-link"
            onClick={() => setMenuOpen(false)}
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
    </div>
  );
}
