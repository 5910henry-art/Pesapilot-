import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  X,
} from "lucide-react";

const ToastContext = createContext(null);

const toastIcons = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

function ToastItem({ item, onDismiss }) {
  const Icon = toastIcons[item.type] || Info;

  useEffect(() => {
    const timer = window.setTimeout(
      () => onDismiss(item.id),
      item.duration
    );

    return () => window.clearTimeout(timer);
  }, [item.id, item.duration, onDismiss]);

  return (
    <div
      className={`app-toast app-toast-${item.type}`}
      role={item.type === "error" ? "alert" : "status"}
      aria-live={item.type === "error" ? "assertive" : "polite"}
    >
      <Icon className="app-toast-icon" size={20} aria-hidden="true" />

      <p className="app-toast-message">{item.message}</p>

      <button
        type="button"
        className="app-toast-close"
        onClick={() => onDismiss(item.id)}
        aria-label="Dismiss notification"
      >
        <X size={17} />
      </button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismissToast = useCallback((id) => {
    setToasts((current) =>
      current.filter((item) => item.id !== id)
    );
  }, []);

  const showToast = useCallback((message, options = {}) => {
    if (typeof message !== "string" || !message.trim()) return;

    const type = ["success", "error", "warning", "info"].includes(
      options.type
    )
      ? options.type
      : "info";

    const duration = Number.isFinite(options.duration)
      ? Math.max(1500, options.duration)
      : 4500;

    setToasts((current) => {
      if (
        current.some(
          (item) =>
            item.message === message.trim() && item.type === type
        )
      ) {
        return current;
      }

      const item = {
        id: nextId.current++,
        message: message.trim(),
        type,
        duration,
      };

      return [...current.slice(-3), item];
    });
  }, []);

  const value = useMemo(
    () => ({ showToast, dismissToast }),
    [showToast, dismissToast]
  );

  return (
    <ToastContext.Provider value={value}>
      {children}

      <div className="app-toast-viewport" aria-label="Notifications">
        {toasts.map((item) => (
          <ToastItem
            key={item.id}
            item={item}
            onDismiss={dismissToast}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);

  if (!context) {
    throw new Error("useToast must be used inside ToastProvider.");
  }

  return context;
}
