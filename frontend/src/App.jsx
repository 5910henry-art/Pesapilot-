import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import { AuthProvider } from "./context/AuthContext";
import { AccountsProvider } from "./context/AccountsContext";
import { ToastProvider } from "./context/ToastContext";
import ProtectedRoute from "./components/ProtectedRoute";
import PlatformOwnerRoute from "./components/PlatformOwnerRoute";

import DashboardLayout from "./layouts/DashboardLayout";
import PlatformOwnerLayout from "./layouts/PlatformOwnerLayout";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Accounts from "./pages/Accounts";
import Transactions from "./pages/Transactions";
import Budgets from "./pages/Budgets";
import Savings from "./pages/Savings";
import Loans from "./pages/Loans";
import PlatformLending from "./pages/PlatformLending";
import PlatformOwnerOverview from "./pages/PlatformOwnerOverview";
import PlatformOwnerProducts from "./pages/PlatformOwnerProducts";
import PlatformOwnerApplications from "./pages/PlatformOwnerApplications";
import PlatformOwnerLoans from "./pages/PlatformOwnerLoans";
import PlatformOwnerInvestors from "./pages/PlatformOwnerInvestors";
import PlatformOwnerUserVerification from "./pages/PlatformOwnerUserVerification";
import PlatformOwnerCollections from "./pages/PlatformOwnerCollections";
import PlatformOwnerFunding from "./pages/PlatformOwnerFunding";
import PlatformOwnerAccounting from "./pages/PlatformOwnerAccounting";
import Bills from "./pages/Bills";
import Expenses from "./pages/Expenses";
import AIManager from "./pages/AIManager";
import Settings from "./pages/Settings";
import WorkspaceInvitation from "./pages/WorkspaceInvitation";
import PWAUpdatePrompt from "./components/PWAUpdatePrompt";

import "./styles/app.css";

export default function App() {
  return (
    <BrowserRouter>
      <PWAUpdatePrompt />
      <ToastProvider>
        <AuthProvider>
          <AccountsProvider>
            <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />

          <Route element={<ProtectedRoute />}>
            <Route path="/workspace-invitation" element={<WorkspaceInvitation />} />
            <Route element={<DashboardLayout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/accounts" element={<Accounts />} />
              <Route path="/transactions" element={<Transactions />} />
              <Route path="/budgets" element={<Budgets />} />
              <Route path="/savings" element={<Savings />} />
              <Route path="/loans" element={<Loans />} />
              <Route path="/lending" element={<PlatformLending />} />
              <Route path="/bills" element={<Bills />} />
              <Route path="/expenses" element={<Expenses />} />
              <Route path="/ai-manager" element={<AIManager />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Route>

          <Route element={<ProtectedRoute />}>
            <Route element={<PlatformOwnerRoute />}>
              <Route element={<PlatformOwnerLayout />}>
                <Route path="/platform-owner" element={<PlatformOwnerOverview />} />
                <Route path="/platform-owner/products" element={<PlatformOwnerProducts />} />
                <Route path="/platform-owner/applications" element={<PlatformOwnerApplications />} />
                <Route path="/platform-owner/loans" element={<PlatformOwnerLoans />} />
                <Route path="/platform-owner/investors" element={<PlatformOwnerInvestors />} />
                <Route path="/platform-owner/user-verification" element={<PlatformOwnerUserVerification />} />
                <Route path="/platform-owner/collections" element={<PlatformOwnerCollections />} />
                <Route path="/platform-owner/funding" element={<PlatformOwnerFunding />} />
                <Route path="/platform-owner/accounting" element={<PlatformOwnerAccounting />} />
              </Route>
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AccountsProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
