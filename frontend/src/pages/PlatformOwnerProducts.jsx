import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw, Pencil, X, Package, AlertCircle } from "lucide-react";
import { platformOwnerApi } from "../api/client";
import { useToast } from "../context/ToastContext";

const EMPTY_FORM = {
  name: "",
  code: "",
  description: "",
  currency_code: "KES",
  min_principal_minor: "10000",
  max_principal_minor: "10000000",
  interest_method: "reducing",
  interest_rate: "10",
  repayment_frequency: "monthly",
  one_time_due_days: "",
  min_term_count: "1",
  max_term_count: "12",
  origination_fee_minor: "0",
  late_payment_fee_minor: "0",
  allow_early_repayment: true,
  is_active: true,
};

const FREQUENCIES = [
  ["weekly", "Weekly"],
  ["biweekly", "Every two weeks"],
  ["monthly", "Monthly"],
  ["quarterly", "Quarterly"],
  ["one_time", "One-time repayment"],
];

function money(minor, currency) {
  const digits = ["JPY", "UGX", "RWF", "BIF", "DJF", "GNF", "KMF", "XAF", "XOF"].includes(currency) ? 0 : 2;
  return new Intl.NumberFormat("en-KE", {
    style: "currency",
    currency: currency || "KES",
    maximumFractionDigits: digits,
  }).format(Number(minor || 0) / (digits === 0 ? 1 : 100));
}

function Field({ label, children, wide = false }) {
  return (
    <label className={`platform-owner-field ${wide ? "platform-owner-field-wide" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function ProductForm({ initial, onClose, onSave, saving }) {
  const { showToast } = useToast();
  const [form, setForm] = useState(initial || EMPTY_FORM);
  const [error, setError] = useState("");

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");

    const integerFields = [
      "min_principal_minor",
      "max_principal_minor",
      "min_term_count",
      "max_term_count",
      "origination_fee_minor",
      "late_payment_fee_minor",
    ];

    const payload = { ...form };
    for (const key of integerFields) {
      const value = Number(payload[key]);
      if (!Number.isSafeInteger(value) || value < 0) {
        setError(`${key.replaceAll("_", " ")} must be a non-negative whole number.`);
        return;
      }
      payload[key] = value;
    }

    if (payload.repayment_frequency === "one_time") {
      const maturityDays = Number(payload.one_time_due_days);
      if (!Number.isSafeInteger(maturityDays) || maturityDays <= 0) {
        setError("One-time maturity period must be a positive whole number of days.");
        return;
      }
      payload.one_time_due_days = maturityDays;
    } else {
      payload.one_time_due_days = null;
    }

    const rate = Number(payload.interest_rate);
    if (!Number.isFinite(rate) || rate < 0) {
      setError("Enter a valid non-negative interest rate.");
      return;
    }

    delete payload.interest_rate;
    payload.interest_rate_percent = rate;
    payload.currency_code = payload.currency_code.trim().toUpperCase();
    payload.name = payload.name.trim();
    payload.code = payload.code.trim().toUpperCase();

    if (!payload.name || !payload.code || !payload.currency_code) {
      setError("Name, code and currency are required.");
      return;
    }

    try {
      await onSave(payload);
    } catch (err) {
      showToast(err.message || "Could not save this product.", { type: "error" });
    }
  }

  return (
    <div className="platform-owner-modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="platform-owner-modal">
        <div className="platform-owner-modal-header">
          <div>
            <span className="lending-eyebrow">PRODUCT CONFIGURATION</span>
            <h2>{initial ? "Edit loan product" : "Create loan product"}</h2>
          </div>
          <button type="button" className="platform-owner-icon-button" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>

        {error && <div className="lending-alert lending-alert-error"><AlertCircle size={17} />{error}</div>}

        <form onSubmit={submit}>
          <div className="platform-owner-form-grid">
            <Field label="Product name">
              <input required maxLength={120} value={form.name} onChange={(e) => update("name", e.target.value)} />
            </Field>
            <Field label="Product code">
              <input required maxLength={40} value={form.code} onChange={(e) => update("code", e.target.value.toUpperCase())} />
            </Field>
            <Field label="Currency code">
              <input required maxLength={3} minLength={3} value={form.currency_code} onChange={(e) => update("currency_code", e.target.value.toUpperCase())} />
            </Field>
            <Field label="Interest method">
              <select value={form.interest_method} onChange={(e) => update("interest_method", e.target.value)}>
                <option value="none">No interest</option>
                <option value="flat">Flat rate</option>
                <option value="reducing">Reducing balance</option>
              </select>
            </Field>
            <Field label="Interest rate (%)">
              <input type="number" min="0" step="0.000001" required value={form.interest_rate} onChange={(e) => update("interest_rate", e.target.value)} />
            </Field>
            <Field label="Repayment frequency">
              <select value={form.repayment_frequency} onChange={(e) => update("repayment_frequency", e.target.value)}>
                {FREQUENCIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            {form.repayment_frequency === "one_time" && (
              <Field label="Maturity period (days)">
                <input
                  type="number"
                  min="1"
                  step="1"
                  required
                  value={form.one_time_due_days}
                  onChange={(e) => update("one_time_due_days", e.target.value)}
                />
              </Field>
            )}
            <Field label="Minimum principal (minor units)">
              <input type="number" min="1" step="1" required value={form.min_principal_minor} onChange={(e) => update("min_principal_minor", e.target.value)} />
            </Field>
            <Field label="Maximum principal (minor units)">
              <input type="number" min="1" step="1" required value={form.max_principal_minor} onChange={(e) => update("max_principal_minor", e.target.value)} />
            </Field>
            <Field label="Minimum term count">
              <input type="number" min="1" step="1" required value={form.min_term_count} onChange={(e) => update("min_term_count", e.target.value)} />
            </Field>
            <Field label="Maximum term count">
              <input type="number" min="1" step="1" required value={form.max_term_count} onChange={(e) => update("max_term_count", e.target.value)} />
            </Field>
            <Field label="Origination fee (minor units)">
              <input type="number" min="0" step="1" value={form.origination_fee_minor} onChange={(e) => update("origination_fee_minor", e.target.value)} />
            </Field>
            <Field label="Late payment fee (minor units)">
              <input type="number" min="0" step="1" value={form.late_payment_fee_minor} onChange={(e) => update("late_payment_fee_minor", e.target.value)} />
            </Field>
            <Field label="Description" wide>
              <textarea rows={3} maxLength={5000} value={form.description} onChange={(e) => update("description", e.target.value)} />
            </Field>
          </div>

          <div className="platform-owner-form-options">
            <label><input type="checkbox" checked={form.allow_early_repayment} onChange={(e) => update("allow_early_repayment", e.target.checked)} /> Allow early repayment</label>
            <label><input type="checkbox" checked={form.is_active} onChange={(e) => update("is_active", e.target.checked)} /> Product active</label>
          </div>

          <p className="platform-owner-form-hint">
            Principal and fee amounts are entered in minor units. For KES, 10000 represents KES 100.00.
          </p>

          <div className="platform-owner-modal-actions">
            <button type="button" className="lending-button lending-button-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="lending-button lending-button-primary" disabled={saving}>
              {saving ? "Saving..." : initial ? "Save changes" : "Create product"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function PlatformOwnerProducts() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { showToast } = useToast();
  const [filter, setFilter] = useState("all");
  const [modal, setModal] = useState(null);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const response = await platformOwnerApi.listProducts({ page: 1, per_page: 100 });
      setProducts(response.products || []);
    } catch (err) {
      showToast(err.message || "Could not load loan products.", { type: "error" });
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  const visibleProducts = products.filter((product) => {
    if (filter === "active") return product.is_active;
    if (filter === "inactive") return !product.is_active;
    return true;
  });

  async function saveProduct(payload) {
    setSaving(true);
    try {
      if (modal.product) {
        await platformOwnerApi.updateProduct(modal.product.id, payload);
        showToast("Loan product updated.", { type: "success" });
      } else {
        await platformOwnerApi.createProduct(payload);
        showToast("Loan product created.", { type: "success" });
      }
      setModal(null);
      await loadProducts();
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(product) {
    const action = product.is_active ? "deactivate" : "activate";
    if (!window.confirm(`Are you sure you want to ${action} ${product.name}?`)) return;

    try {
      await platformOwnerApi.updateProduct(product.id, { is_active: !product.is_active });
      showToast(`Product ${action}d.`, { type: "success" });
      await loadProducts();
    } catch (err) {
      showToast(err.message || `Could not ${action} product.`, { type: "error" });
    }
  }

  function editInitial(product) {
    return {
      name: product.name || "",
      code: product.code || "",
      description: product.description || "",
      currency_code: product.currency_code || "KES",
      min_principal_minor: String(product.min_principal_minor ?? 0),
      max_principal_minor: String(product.max_principal_minor ?? 0),
      interest_method: product.interest_method || "none",
      interest_rate: String(product.interest_rate_percent ?? 0),
      repayment_frequency: product.repayment_frequency || "monthly",
      one_time_due_days: product.one_time_due_days == null
        ? ""
        : String(product.one_time_due_days),
      min_term_count: String(product.min_term_count ?? 1),
      max_term_count: String(product.max_term_count ?? 1),
      origination_fee_minor: String(product.origination_fee_minor ?? 0),
      late_payment_fee_minor: String(product.late_payment_fee_minor ?? 0),
      allow_early_repayment: product.allow_early_repayment === true,
      is_active: product.is_active === true,
    };
  }

  return (
    <div className="platform-owner-page lending-page">
      <div className="lending-header">
        <div>
          <span className="lending-eyebrow"><Package size={14} /> LENDING CONFIGURATION</span>
          <h1>Loan Products</h1>
          <p>Create and manage the products customers can apply for.</p>
        </div>
        <button className="lending-button lending-button-primary" onClick={() => setModal({ product: null })}>
          <Plus size={17} /> New product
        </button>
      </div>


      <div className="platform-owner-toolbar">
        <div className="platform-owner-filter-tabs">
          {["all", "active", "inactive"].map((value) => (
            <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>
              {value.charAt(0).toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
        <button className="lending-button lending-button-secondary" onClick={loadProducts} disabled={loading}>
          <RefreshCw size={15} className={loading ? "lending-spin" : ""} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="platform-owner-empty">Loading loan products...</div>
      ) : visibleProducts.length === 0 ? (
        <div className="platform-owner-empty">
          <Package size={30} />
          <h3>No {filter === "all" ? "" : filter} products found</h3>
          <p>Create a loan product to make it available to customers.</p>
        </div>
      ) : (
        <div className="platform-owner-product-grid">
          {visibleProducts.map((product) => (
            <article className="platform-owner-product-card" key={product.id}>
              <div className="platform-owner-product-top">
                <div className="platform-owner-product-icon"><Package size={20} /></div>
                <span className={`platform-owner-status ${product.is_active ? "is-active" : "is-inactive"}`}>
                  {product.is_active ? "Active" : "Inactive"}
                </span>
              </div>
              <div className="platform-owner-product-title">
                <h3>{product.name}</h3>
                <span>{product.code} · {product.currency_code}</span>
              </div>
              <p className="platform-owner-product-description">{product.description || "No description provided."}</p>
              <div className="platform-owner-product-details">
                <div><span>Principal range</span><strong>{money(product.min_principal_minor, product.currency_code)} – {money(product.max_principal_minor, product.currency_code)}</strong></div>
                <div><span>Interest</span><strong>{product.interest_method === "none" ? "No interest" : `${product.interest_rate_percent}% · ${product.interest_method}`}</strong></div>
                <div>
                  <span>Repayment</span>
                  <strong>
                    {product.repayment_frequency === "one_time"
                      ? `One-time · ${product.one_time_due_days ?? "Not configured"} days`
                      : product.repayment_frequency}
                  </strong>
                </div>
                <div><span>Term range</span><strong>{product.min_term_count} – {product.max_term_count}</strong></div>
              </div>
              <div className="platform-owner-product-actions">
                <button className="lending-button lending-button-secondary" onClick={() => setModal({ product })}>
                  <Pencil size={14} /> Edit
                </button>
                <button className={`lending-button ${product.is_active ? "lending-button-danger" : "lending-button-primary"}`} onClick={() => toggleActive(product)}>
                  {product.is_active ? "Deactivate" : "Activate"}
                </button>
              </div>

            </article>
          ))}
        </div>
      )}

      {modal && (
        <ProductForm
          key={modal.product?.id || "new"}
          initial={modal.product ? editInitial(modal.product) : null}
          onClose={() => setModal(null)}
          onSave={saveProduct}
          saving={saving}
        />
      )}
    </div>
  );
}
