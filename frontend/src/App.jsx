import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import { AuthProvider } from "./context/AuthContext";
import { AccountsProvider } from "./context/AccountsContext";
import ProtectedRoute from "./components/ProtectedRoute";

import DashboardLayout from "./layouts/DashboardLayout";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Accounts from "./pages/Accounts";
import Transactions from "./pages/Transactions";
import Budgets from "./pages/Budgets";
import Savings from "./pages/Savings";
import Loans from "./pages/Loans";
import Bills from "./pages/Bills";
import Expenses from "./pages/Expenses";
import AIManager from "./pages/AIManager";

import "./styles/app.css";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AccountsProvider>
          <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />

          <Route element={<ProtectedRoute />}>
            <Route element={<DashboardLayout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/accounts" element={<Accounts />} />
              <Route path="/transactions" element={<Transactions />} />
              <Route path="/budgets" element={<Budgets />} />
              <Route path="/savings" element={<Savings />} />
              <Route path="/loans" element={<Loans />} />
              <Route path="/bills" element={<Bills />} />
              <Route path="/expenses" element={<Expenses />} />
              <Route path="/ai-manager" element={<AIManager />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AccountsProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
