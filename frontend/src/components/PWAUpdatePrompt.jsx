import { useEffect } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

export default function PWAUpdatePrompt() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisterError(error) {
      console.error("PesaPilot service worker registration failed:", error);
    },
  });

  useEffect(() => {
    if (!import.meta.env.DEV || !("serviceWorker" in navigator)) {
      return undefined;
    }

    navigator.serviceWorker
      .register("/dev-sw.js", { type: "module" })
      .catch((error) => {
        console.error(
          "PesaPilot development service worker registration failed:",
          error,
        );
      });
  }, []);

  if (!needRefresh) return null;

  return (
    <div className="pwa-update-prompt" role="status">
      <div>
        <strong>PesaPilot update available</strong>
        <p>A new version is ready to install.</p>
      </div>

      <div className="pwa-update-actions">
        <button
          type="button"
          onClick={() => updateServiceWorker(true)}
        >
          Update
        </button>
        <button
          type="button"
          className="pwa-update-dismiss"
          onClick={() => setNeedRefresh(false)}
          aria-label="Dismiss update notification"
        >
          Later
        </button>
      </div>
    </div>
  );
}
