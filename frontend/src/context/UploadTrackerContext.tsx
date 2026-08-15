"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import UploadDetailModal from "@/app/admin/sections/UploadDetailModal";
import {
  cancelUpload,
  completeMultipartUpload,
  getUploadStatus,
  initiateMultipartUpload,
  markPartUploaded,
  presignUploadPart,
  uploadDatasetFile,
  uploadPartDirect,
} from "@/lib/api/admin-datasets";
import { ApiError } from "@/lib/api/client";

const POLL_INTERVAL_MS = 1500;

// Files at or above this size go through direct-to-MinIO multipart upload
// (Phase 1) instead of the single-request path — proxying a genuinely
// large file through this backend process wastes its bandwidth/CPU for no
// benefit once a real multipart alternative exists.
const MULTIPART_THRESHOLD_BYTES = 20 * 1024 * 1024;

export type TrackedUploadStatus =
  | "uploading"
  | "processing"
  | "complete"
  | "failed"
  | "cancelled";

export interface TrackedUpload {
  /** Local tracking key — set to the real Upload.id as soon as one exists
   * (immediately for small files, after multipart initiate for large
   * ones). Stable across the whole lifecycle once assigned. */
  uploadId: string | null;
  /** Stable client-side key that exists even before uploadId does, so the
   * UI has something to key/reference before the server has responded. */
  localKey: string;
  datasetId: string;
  datasetTitle: string;
  fileName: string;
  status: TrackedUploadStatus;
  uploadPct: number | null;
  uploadedBytes: number | null;
  totalBytes: number | null;
  processingStage: string | null;
  processingPct: number | null;
  statusText: string | null;
  errorMessage: string | null;
}

interface UploadTrackerContextValue {
  uploads: TrackedUpload[];
  /** Detail-view visibility: which tracked upload (by localKey) the user
   * currently has the detailed modal open for, if any. Minimizing sets
   * this to null without touching the underlying transfer/tracking. */
  openDetailKey: string | null;
  openDetail: (localKey: string) => void;
  closeDetail: () => void;
  /** onUploaded is called once, when this specific upload reaches
   * "complete" — typically wired to whichever admin section's list
   * should refetch. Stored per-upload since the section that started it
   * may not be the one mounted when it finishes. */
  startUpload: (args: {
    datasetId: string;
    datasetTitle: string;
    file: File;
    onUploaded?: () => void;
  }) => string;
  /** Registers an upload the SERVER already started (Bulk Import — the
   * transfer runs as a background Celery task, not a browser-driven
   * multipart/single-request flow) for polling/display through the exact
   * same tracker UI. No uploadPct is ever shown for these (there's no
   * client-side transfer happening) — only processingStage/processingPct
   * once ingestion begins, same as every other upload once it reaches
   * that stage. */
  trackServerUpload: (args: {
    uploadId: string;
    datasetId: string;
    datasetTitle: string;
    fileName: string;
    onUploaded?: () => void;
  }) => string;
  cancelTrackedUpload: (localKey: string) => Promise<void>;
  dismissUpload: (localKey: string) => void;
}

const UploadTrackerContext = createContext<UploadTrackerContextValue | undefined>(undefined);

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function UploadTrackerProvider({ children }: { children: React.ReactNode }) {
  const [uploads, setUploads] = useState<TrackedUpload[]>([]);
  const [openDetailKey, setOpenDetailKey] = useState<string | null>(null);

  // Per-upload abort/poll state, keyed by localKey — lives in refs (not
  // React state) since it's plumbing, not render data, and must survive
  // whichever component happens to be mounted/unmounted around it.
  const abortControllers = useRef<Map<string, AbortController>>(new Map());
  const abortFlags = useRef<Map<string, boolean>>(new Map());
  const pollIntervals = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  const onUploadedCallbacks = useRef<Map<string, () => void>>(new Map());

  const updateUpload = useCallback((localKey: string, patch: Partial<TrackedUpload>) => {
    setUploads((prev) => prev.map((u) => (u.localKey === localKey ? { ...u, ...patch } : u)));
  }, []);

  const openDetail = useCallback((localKey: string) => setOpenDetailKey(localKey), []);
  const closeDetail = useCallback(() => setOpenDetailKey(null), []);

  const dismissUpload = useCallback((localKey: string) => {
    const interval = pollIntervals.current.get(localKey);
    if (interval) clearInterval(interval);
    pollIntervals.current.delete(localKey);
    abortControllers.current.delete(localKey);
    abortFlags.current.delete(localKey);
    onUploadedCallbacks.current.delete(localKey);
    setUploads((prev) => prev.filter((u) => u.localKey !== localKey));
    setOpenDetailKey((k) => (k === localKey ? null : k));
  }, []);

  const pollProcessing = useCallback(
    (localKey: string, uploadId: string) => {
      const interval = setInterval(async () => {
        try {
          const upload = await getUploadStatus(uploadId);
          const isTransferring = upload.status === "initiated" || upload.status === "queued";
          updateUpload(localKey, {
            processingStage: upload.progress_stage,
            processingPct: upload.progress_pct,
            // Bulk Import's server-side transfer (Admin Panel production-
            // readiness follow-up) reports real progress through these
            // same uploaded_bytes/total_size_bytes fields Phase 1's
            // browser multipart upload already populates — surfacing them
            // here means the tracker shows a genuine transfer percentage
            // for a bulk import too, not just once ingestion starts.
            uploadPct:
              isTransferring && upload.total_size_bytes
                ? Math.min(100, Math.round((upload.uploaded_bytes / upload.total_size_bytes) * 100))
                : null,
            uploadedBytes: isTransferring ? upload.uploaded_bytes : null,
            totalBytes: isTransferring ? upload.total_size_bytes : null,
          });

          if (upload.status === "complete") {
            clearInterval(interval);
            pollIntervals.current.delete(localKey);
            updateUpload(localKey, { status: "complete", statusText: "Complete." });
            onUploadedCallbacks.current.get(localKey)?.();
          } else if (upload.status === "failed") {
            clearInterval(interval);
            pollIntervals.current.delete(localKey);
            updateUpload(localKey, {
              status: "failed",
              statusText: upload.error_message ?? "Upload failed.",
              errorMessage: upload.error_message ?? "Upload processing failed.",
            });
          } else if (upload.status === "cancelled") {
            clearInterval(interval);
            pollIntervals.current.delete(localKey);
            updateUpload(localKey, { status: "cancelled", statusText: "Cancelled." });
          } else if (isTransferring) {
            updateUpload(localKey, { statusText: "Transferring to storage…" });
          } else {
            updateUpload(localKey, {
              statusText: `Processing: ${upload.progress_stage ?? upload.status}…`,
            });
          }
        } catch {
          clearInterval(interval);
          pollIntervals.current.delete(localKey);
          updateUpload(localKey, {
            status: "failed",
            statusText: "Failed to check upload status.",
            errorMessage: "Failed to check upload status.",
          });
        }
      }, POLL_INTERVAL_MS);
      pollIntervals.current.set(localKey, interval);
    },
    [updateUpload]
  );

  const runSmallFileUpload = useCallback(
    async (localKey: string, datasetId: string, file: File) => {
      const controller = new AbortController();
      abortControllers.current.set(localKey, controller);
      updateUpload(localKey, { statusText: "Uploading…" });
      try {
        const result = await uploadDatasetFile(datasetId, file, { signal: controller.signal });
        abortControllers.current.delete(localKey);
        updateUpload(localKey, {
          uploadId: result.upload.id,
          status: "processing",
          statusText: "Processing…",
        });
        pollProcessing(localKey, result.upload.id);
      } catch (err) {
        abortControllers.current.delete(localKey);
        if (err instanceof DOMException && err.name === "AbortError") {
          updateUpload(localKey, { status: "cancelled", statusText: "Cancelled." });
          return;
        }
        updateUpload(localKey, {
          status: "failed",
          statusText: err instanceof ApiError ? err.message : "Failed to upload file.",
          errorMessage: err instanceof ApiError ? err.message : "Failed to upload file.",
        });
      }
    },
    [pollProcessing, updateUpload]
  );

  const runLargeFileUpload = useCallback(
    async (localKey: string, datasetId: string, file: File) => {
      updateUpload(localKey, { statusText: "Starting upload…" });
      try {
        const { upload, total_parts, part_size_bytes } = await initiateMultipartUpload(
          datasetId,
          file.name,
          file.size
        );
        updateUpload(localKey, {
          uploadId: upload.id,
          totalBytes: file.size,
          uploadedBytes: 0,
          uploadPct: 0,
        });

        let bytesDone = 0;
        for (let partNumber = 1; partNumber <= total_parts; partNumber++) {
          if (abortFlags.current.get(localKey)) return;

          const start = (partNumber - 1) * part_size_bytes;
          const end = Math.min(start + part_size_bytes, file.size);
          const blob = file.slice(start, end);

          const { upload_url } = await presignUploadPart(upload.id, partNumber);
          await uploadPartDirect(upload_url, blob);
          await markPartUploaded(upload.id, partNumber, blob.size);

          bytesDone += blob.size;
          const pct = Math.round((bytesDone / file.size) * 100);
          updateUpload(localKey, {
            uploadedBytes: bytesDone,
            uploadPct: pct,
            statusText: `Uploading… ${pct}%`,
          });
        }

        if (abortFlags.current.get(localKey)) return;

        updateUpload(localKey, { statusText: "Finalizing upload…" });
        const { upload: completedUpload } = await completeMultipartUpload(upload.id);
        updateUpload(localKey, { status: "processing", statusText: "Processing…" });
        pollProcessing(localKey, completedUpload.id);
      } catch (err) {
        if (abortFlags.current.get(localKey)) return;
        updateUpload(localKey, {
          status: "failed",
          statusText: err instanceof ApiError ? err.message : "Failed to upload file.",
          errorMessage: err instanceof ApiError ? err.message : "Failed to upload file.",
        });
      }
    },
    [pollProcessing, updateUpload]
  );

  const startUpload = useCallback(
    ({
      datasetId,
      datasetTitle,
      file,
      onUploaded,
    }: {
      datasetId: string;
      datasetTitle: string;
      file: File;
      onUploaded?: () => void;
    }) => {
      const localKey = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      abortFlags.current.set(localKey, false);
      if (onUploaded) onUploadedCallbacks.current.set(localKey, onUploaded);

      const entry: TrackedUpload = {
        uploadId: null,
        localKey,
        datasetId,
        datasetTitle,
        fileName: file.name,
        status: "uploading",
        uploadPct: null,
        uploadedBytes: null,
        totalBytes: null,
        processingStage: null,
        processingPct: null,
        statusText: "Starting…",
        errorMessage: null,
      };
      setUploads((prev) => [...prev, entry]);
      setOpenDetailKey(localKey);

      if (file.size >= MULTIPART_THRESHOLD_BYTES) {
        runLargeFileUpload(localKey, datasetId, file);
      } else {
        runSmallFileUpload(localKey, datasetId, file);
      }
      return localKey;
    },
    [runLargeFileUpload, runSmallFileUpload]
  );

  const trackServerUpload = useCallback(
    ({
      uploadId,
      datasetId,
      datasetTitle,
      fileName,
      onUploaded,
    }: {
      uploadId: string;
      datasetId: string;
      datasetTitle: string;
      fileName: string;
      onUploaded?: () => void;
    }) => {
      const localKey = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      abortFlags.current.set(localKey, false);
      if (onUploaded) onUploadedCallbacks.current.set(localKey, onUploaded);

      const entry: TrackedUpload = {
        uploadId,
        localKey,
        datasetId,
        datasetTitle,
        fileName,
        status: "processing",
        uploadPct: null,
        uploadedBytes: null,
        totalBytes: null,
        processingStage: null,
        processingPct: null,
        statusText: "Importing on server…",
        errorMessage: null,
      };
      setUploads((prev) => [...prev, entry]);
      setOpenDetailKey(localKey);
      pollProcessing(localKey, uploadId);
      return localKey;
    },
    [pollProcessing]
  );

  const cancelTrackedUpload = useCallback(
    async (localKey: string) => {
      const upload = uploads.find((u) => u.localKey === localKey);
      if (!upload) return;

      abortFlags.current.set(localKey, true);
      const controller = abortControllers.current.get(localKey);
      if (controller) controller.abort();

      const interval = pollIntervals.current.get(localKey);
      if (interval) clearInterval(interval);

      if (!upload.uploadId) {
        // Small-file transfer still in flight, no server-side Upload row
        // exists yet — the AbortController above is the entire
        // cancellation; nothing server-side to reach.
        updateUpload(localKey, { status: "cancelled", statusText: "Cancelled." });
        return;
      }

      try {
        await cancelUpload(upload.uploadId);
        updateUpload(localKey, { status: "cancelled", statusText: "Cancelled." });
      } catch (err) {
        updateUpload(localKey, {
          errorMessage: err instanceof ApiError ? err.message : "Failed to cancel upload.",
        });
        throw err;
      }
    },
    [uploads, updateUpload]
  );

  return (
    <UploadTrackerContext.Provider
      value={{
        uploads,
        openDetailKey,
        openDetail,
        closeDetail,
        startUpload,
        trackServerUpload,
        cancelTrackedUpload,
        dismissUpload,
      }}
    >
      {children}
      <UploadTrackerIndicator />
      <UploadTrackerDetailModal />
    </UploadTrackerContext.Provider>
  );
}

/** Renders the detail/progress modal for whichever upload currently has
 * openDetailKey set — owned entirely by the tracker, not by whichever
 * admin section happened to call startUpload, so it stays visible/
 * reopenable across navigation. */
function UploadTrackerDetailModal() {
  const ctx = useContext(UploadTrackerContext);
  if (!ctx) return null;
  const { uploads, openDetailKey } = ctx;
  const tracked = uploads.find((u) => u.localKey === openDetailKey);
  if (!tracked) return null;
  return <UploadDetailModal localKey={tracked.localKey} />;
}

/** Persistent, always-mounted floating indicator — visible whenever at
 * least one upload is active/recently finished, regardless of which admin
 * section (or non-admin page) is currently showing. Clicking it reopens
 * the detail view for that upload; it never itself cancels anything. */
function UploadTrackerIndicator() {
  const ctx = useContext(UploadTrackerContext);
  if (!ctx) return null;
  const { uploads, openDetailKey, openDetail } = ctx;

  const visible = uploads.filter((u) => u.localKey !== openDetailKey);
  if (visible.length === 0) return null;

  return (
    <div className="upload-tracker-stack">
      {visible.map((u) => {
        const pct =
          u.status === "processing"
            ? (u.processingPct ?? null)
            : u.status === "uploading"
              ? u.uploadPct
              : null;
        return (
          <button
            key={u.localKey}
            className={`upload-tracker-pill upload-tracker-${u.status}`}
            onClick={() => openDetail(u.localKey)}
            title={`${u.fileName} — ${u.statusText ?? u.status}`}
          >
            <span className="upload-tracker-icon">
              {u.status === "complete" ? "✓" : u.status === "failed" ? "⚠" : u.status === "cancelled" ? "✕" : "⬆"}
            </span>
            <span className="upload-tracker-text">
              <span className="upload-tracker-filename">{u.fileName}</span>
              <span className="upload-tracker-status">{u.statusText ?? u.status}</span>
            </span>
            {pct !== null && (
              <span className="upload-tracker-pct">{pct}%</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function useUploadTracker() {
  const ctx = useContext(UploadTrackerContext);
  if (!ctx) throw new Error("useUploadTracker must be used within an UploadTrackerProvider");
  return ctx;
}

export { formatBytes };
