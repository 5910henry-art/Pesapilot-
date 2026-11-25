import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  BellOff,
  Check,
  CheckCheck,
  ChevronLeft,
  LoaderCircle,
  Settings,
  Smartphone,
  X,
} from "lucide-react";
import { apiRequest } from "../api/client";

const CATEGORIES = [
  ["loan_repayments", "Loan repayments"],
  ["loan_due_reminders", "Loan due-date reminders"],
  ["loan_application_updates", "Loan application updates"],
  ["bill_reminders", "Bill reminders"],
  ["budget_alerts", "Budget alerts"],
  ["savings_milestones", "Savings milestones"],
  ["account_security", "Account security"],
];

function decodeVapidKey(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

function safeTarget(value) {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.startsWith("/api/")
  ) {
    return "/";
  }

  return value;
}

function formatDate(value) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString();
}

export default function NotificationCenter() {
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [showPreferences, setShowPreferences] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [preferences, setPreferences] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushSupported, setPushSupported] = useState(false);
  const [pushSubscription, setPushSubscription] = useState(null);
  const [error, setError] = useState("");

  const loadNotifications = useCallback(async () => {
    try {
      const result = await apiRequest("/notifications?limit=30");

      setNotifications(result.notifications || []);
      setUnreadCount(result.unread_count || 0);
      setError("");
    } catch (err) {
      setError(err.message || "Could not load notifications.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPreferences = useCallback(async () => {
    try {
      const result = await apiRequest("/notifications/preferences");
      setPreferences(result.preferences || []);
    } catch (err) {
      setError(err.message || "Could not load notification preferences.");
    }
  }, []);

  useEffect(() => {
    loadNotifications();
    loadPreferences();

    const interval = window.setInterval(loadNotifications, 60000);

    function handleFocus() {
      loadNotifications();
    }

    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        loadNotifications();
      }
    }

    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [loadNotifications, loadPreferences]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    loadNotifications();

    const interval = window.setInterval(loadNotifications, 15000);

    return () => window.clearInterval(interval);
  }, [open, loadNotifications]);

  useEffect(() => {
    const supported =
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window;

    setPushSupported(supported);

    if (!supported) {
      return;
    }

    let active = true;

    navigator.serviceWorker.ready
      .then((registration) => registration.pushManager.getSubscription())
      .then((subscription) => {
        if (active) {
          setPushSubscription(subscription);
        }
      })
      .catch(() => {
        if (active) {
          setPushSubscription(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function markRead(notification) {
    if (notification.is_read) {
      navigate(safeTarget(notification.target_url));
      setOpen(false);
      return;
    }

    try {
      await apiRequest(
        `/notifications/${notification.id}/read`,
        { method: "PATCH" },
      );

      setNotifications((current) =>
        current.map((item) =>
          item.id === notification.id
            ? { ...item, is_read: true }
            : item,
        ),
      );

      setUnreadCount((count) => Math.max(0, count - 1));
      navigate(safeTarget(notification.target_url));
      setOpen(false);
    } catch (err) {
      setError(err.message || "Could not update notification.");
    }
  }

  async function markAllRead() {
    setBusy(true);
    setError("");

    try {
      await apiRequest("/notifications/read-all", {
        method: "POST",
      });

      setNotifications((current) =>
        current.map((item) => ({ ...item, is_read: true })),
      );
      setUnreadCount(0);
    } catch (err) {
      setError(err.message || "Could not mark notifications as read.");
    } finally {
      setBusy(false);
    }
  }

  async function updatePreference(category, field, value) {
    const previous = preferences;

    const updated = preferences.map((item) =>
      item.category === category
        ? { ...item, [field]: value }
        : item,
    );

    setPreferences(updated);
    setError("");

    try {
      const result = await apiRequest("/notifications/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preferences: [
            { category, [field]: value },
          ],
        }),
      });

      setPreferences(result.preferences || updated);
      await loadNotifications();
    } catch (err) {
      setPreferences(previous);
      setError(err.message || "Could not save notification preference.");
    }
  }

  async function enablePush() {
    setPushBusy(true);
    setError("");

    let subscription = null;

    try {
      setError("Requesting notification permission...");
      const permission = await Notification.requestPermission();

      if (permission !== "granted") {
        throw new Error("Allow notifications in your browser settings first.");
      }

      setError("Fetching push configuration...");
      const keyResult = await apiRequest("/notifications/push/public-key");

      if (!keyResult.public_key) {
        throw new Error("Push notifications are not configured on the server.");
      }

      setError("Checking service worker registration...");
      const registrations = await navigator.serviceWorker.getRegistrations();
      const activeRegistration = registrations.find(
        (item) => item.active,
      );

      const registration = activeRegistration || await Promise.race([
        navigator.serviceWorker.ready,
        new Promise((_, reject) =>
          window.setTimeout(
            () => reject(new Error(
              "No active service worker registered after 10 seconds. Check PWA registration.",
            )),
            10000,
          ),
        ),
      ]);

      if (!registration.active) {
        throw new Error("The service worker has no active worker.");
      }

      setError("Creating device push subscription...");
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: decodeVapidKey(keyResult.public_key),
      });

      setError("Saving device subscription...");
      await apiRequest("/notifications/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription.toJSON()),
      });

      setPushSubscription(subscription);
    } catch (err) {
      if (subscription) {
        await subscription.unsubscribe().catch(() => {});
      }

      setError(err.message || "Could not enable device notifications.");
    } finally {
      setPushBusy(false);
    }
  }

  async function disablePush() {
    setPushBusy(true);
    setError("");

    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();

      if (subscription) {
        await apiRequest("/notifications/push/subscribe", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        });

        await subscription.unsubscribe();
      }

      setPushSubscription(null);
    } catch (err) {
      setError(err.message || "Could not disable device notifications.");
    } finally {
      setPushBusy(false);
    }
  }

  const categoryNames = Object.fromEntries(CATEGORIES);

  return (
    <div className="notification-center">
      <button
        type="button"
        className="icon-button notification-trigger"
        aria-label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ""}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Bell size={19} />
        {unreadCount > 0 && (
          <span className="notification-count">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <section
          className="notification-panel"
          aria-label="Notification centre"
        >
          <header className="notification-panel-header">
            <div>
              <h2>
                {showPreferences ? "Notification settings" : "Notifications"}
              </h2>
              {!showPreferences && (
                <p>
                  {unreadCount
                    ? `${unreadCount} unread`
                    : "You're all caught up"}
                </p>
              )}
            </div>

            <div className="notification-panel-actions">
              {showPreferences ? (
                <button
                  type="button"
                  aria-label="Back to notifications"
                  onClick={() => setShowPreferences(false)}
                >
                  <ChevronLeft size={19} />
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    aria-label="Notification settings"
                    onClick={() => setShowPreferences(true)}
                  >
                    <Settings size={18} />
                  </button>
                  <button
                    type="button"
                    aria-label="Close notifications"
                    onClick={() => setOpen(false)}
                  >
                    <X size={19} />
                  </button>
                </>
              )}
            </div>
          </header>

          {error && (
            <p className="notification-error" role="alert">
              {error}
            </p>
          )}

          {showPreferences ? (
            <div className="notification-preferences">
              <div className="notification-push-setting">
                <div className="notification-setting-icon">
                  <Smartphone size={19} />
                </div>
                <div className="notification-setting-copy">
                  <strong>Device notifications</strong>
                  <p>
                    Receive notifications even when PesaPilot is not open.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={!pushSupported || pushBusy}
                  onClick={pushSubscription ? disablePush : enablePush}
                >
                  {pushBusy ? (
                    <LoaderCircle size={16} className="notification-spin" />
                  ) : pushSubscription ? (
                    "Disable"
                  ) : (
                    "Enable"
                  )}
                </button>
              </div>

              {!pushSupported && (
                <p className="notification-muted">
                  This browser does not support push notifications.
                </p>
              )}

              <h3>Notification categories</h3>
              <p className="notification-muted">
                Choose which notifications appear in the app and on this device.
              </p>

              {CATEGORIES.map(([category, label]) => {
                const preference = preferences.find(
                  (item) => item.category === category,
                ) || {
                  in_app_enabled: true,
                  push_enabled: true,
                };

                return (
                  <div className="notification-preference-row" key={category}>
                    <strong>{label}</strong>
                    <label>
                      <input
                        type="checkbox"
                        checked={preference.in_app_enabled}
                        onChange={(event) =>
                          updatePreference(
                            category,
                            "in_app_enabled",
                            event.target.checked,
                          )
                        }
                      />
                      In-app
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={preference.push_enabled}
                        onChange={(event) =>
                          updatePreference(
                            category,
                            "push_enabled",
                            event.target.checked,
                          )
                        }
                      />
                      Push
                    </label>
                  </div>
                );
              })}
            </div>
          ) : (
            <>
              {unreadCount > 0 && (
                <div className="notification-list-toolbar">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={markAllRead}
                  >
                    <CheckCheck size={15} />
                    Mark all as read
                  </button>
                </div>
              )}

              <div className="notification-list">
                {loading ? (
                  <div className="notification-empty">
                    <LoaderCircle size={22} className="notification-spin" />
                    <span>Loading notifications</span>
                  </div>
                ) : notifications.length === 0 ? (
                  <div className="notification-empty">
                    <BellOff size={25} />
                    <strong>No notifications yet</strong>
                    <span>New updates will appear here.</span>
                  </div>
                ) : (
                  notifications.map((notification) => (
                    <article
                      className={`notification-item ${
                        notification.is_read ? "is-read" : "is-unread"
                      }`}
                      key={notification.id}
                    >
                      <button
                        type="button"
                        className="notification-item-content"
                        onClick={() => markRead(notification)}
                      >
                        <span className="notification-item-heading">
                          <strong>{notification.title}</strong>
                          {!notification.is_read && (
                            <span className="notification-unread-indicator" />
                          )}
                        </span>
                        <span className="notification-item-body">
                          {notification.body}
                        </span>
                        <span className="notification-item-meta">
                          {categoryNames[notification.category] ||
                            "Notification"}
                          {" · "}
                          {formatDate(notification.created_at)}
                        </span>
                      </button>

                      {!notification.is_read && (
                        <button
                          type="button"
                          className="notification-mark-read"
                          aria-label="Mark as read"
                          onClick={async () => {
                            try {
                              await apiRequest(
                                `/notifications/${notification.id}/read`,
                                { method: "PATCH" },
                              );

                              setNotifications((current) =>
                                current.map((item) =>
                                  item.id === notification.id
                                    ? { ...item, is_read: true }
                                    : item,
                                ),
                              );
                              setUnreadCount((count) =>
                                Math.max(0, count - 1),
                              );
                            } catch (err) {
                              setError(
                                err.message || "Could not update notification.",
                              );
                            }
                          }}
                        >
                          <Check size={16} />
                        </button>
                      )}
                    </article>
                  ))
                )}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
