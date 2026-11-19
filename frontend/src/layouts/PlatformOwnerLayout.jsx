import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Package,
  ClipboardList,
  BriefcaseBusiness,
  Wallet,
  Users,
  Landmark,
  BookOpen,
  Menu,
  X,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  CircleUserRound,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import SessionExpiryGuard from "../components/SessionExpiryGuard";

const navigation = [
  { label: "Overview", icon: LayoutDashboard, path: "/platform-owner", end: true },
  { label: "Loan Products", icon: Package, path: "/platform-owner/products" },
  { label: "Applications", icon: ClipboardList, path: "/platform-owner/applications" },
  { label: "Loan Portfolio", icon: BriefcaseBusiness, path: "/platform-owner/loans" },
  { label: "Collections", icon: Wallet, path: "/platform-owner/collections" },
  { label: "Investors", icon: Users, path: "/platform-owner/investors" },
  { label: "Funding", icon: Landmark, path: "/platform-owner/funding" },
  { label: "Accounting", icon: BookOpen, path: "/platform-owner/accounting" },
];

export default function PlatformOwnerLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const { user, profile, logout } = useAuth();
  const navigate = useNavigate();

  const fullName = [profile?.first_name, profile?.last_name]
    .filter(Boolean)
    .join(" ") || user?.email || "Platform owner";

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
    <div className={`app-shell platform-owner-shell ${sidebarCollapsed ? "sidebar-is-collapsed" : ""}`}>
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
            <span>PLATFORM ADMINISTRATION</span>
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

        <div className="workspace-label">ADMINISTRATION</div>

        <div className="workspace-switcher">
          <div className="workspace-avatar">
            <ShieldCheck size={19} />
          </div>
          <div className="workspace-info">
            <strong>Platform owner</strong>
            <span>PesaPilot Lending Business</span>
          </div>
        </div>

        <div className="nav-label">MANAGEMENT</div>

        <nav className="navigation">
          {navigation.map(({ label, icon: Icon, path, end }) => (
            <NavLink
              key={path}
              to={path}
              end={Boolean(end)}
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
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-profile">
            <div className="profile-avatar">
              {fullName.charAt(0).toUpperCase()}
            </div>

            <div className="profile-info">
              <strong>{fullName}</strong>
              <span>{user?.email || "Owner account"}</span>
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
            <span>Platform</span>
            <span className="breadcrumb-separator">/</span>
            <strong>Administration</strong>
          </div>

          <div className="topbar-actions">
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
