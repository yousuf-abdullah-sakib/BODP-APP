import { useRef, useState } from "react";
import { getExtractionDownloadUrl, getExtractionStatus, startExtraction } from "@/lib/api/requests";
import { useToast } from "@/context/ToastContext";
import type { ExtractionFormat, GrantSummary } from "@/lib/types/requests";

const POLL_INTERVAL_MS = 1500;

/**
 * Drives the "Download" flow for a single grant: start an extraction with
 * the grant's own scope (Master Plan §3 Phase 5 — no sub-narrowing UI in
 * this phase, just format choice), poll until it's complete or failed,
 * then fetch the presigned URL and navigate the browser to it directly —
 * the URL is self-authenticating (signed), so a plain navigation works
 * without needing to attach the app's Authorization header.
 */
export function useExtractionDownload() {
  const { toast } = useToast();
  const [downloadingGrantId, setDownloadingGrantId] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function stopPolling() {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }

  async function download(grant: GrantSummary, format: ExtractionFormat) {
    stopPolling();
    setDownloadingGrantId(grant.id);
    try {
      const extraction = await startExtraction(grant.id, { scope: grant.scope ?? {}, format });
      await poll(extraction.id);
    } catch {
      setDownloadingGrantId(null);
      toast("Failed to start the extraction. Please try again.", "error");
    }
  }

  function poll(extractionId: string) {
    return new Promise<void>((resolve) => {
      const check = async () => {
        try {
          const status = await getExtractionStatus(extractionId);
          if (status.status === "complete") {
            setDownloadingGrantId(null);
            const { download_url } = await getExtractionDownloadUrl(extractionId);
            toast("Download ready.", "success");
            window.location.href = download_url;
            resolve();
            return;
          }
          if (status.status === "failed") {
            setDownloadingGrantId(null);
            toast(status.error_message || "Extraction failed.", "error");
            resolve();
            return;
          }
          pollTimer.current = setTimeout(check, POLL_INTERVAL_MS);
        } catch {
          setDownloadingGrantId(null);
          toast("Lost track of the extraction. Please try again.", "error");
          resolve();
        }
      };
      check();
    });
  }

  return { downloadingGrantId, download };
}
