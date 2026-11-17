"use client";

import { Clapperboard, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { FacebookIcon, YouTubeIcon } from "@/components/publishing/kinds";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { InlineAlert } from "@/components/ui/states";
import { errorMessage } from "@/lib/api";
import type { VideoOut } from "@/lib/types";
import { formatSeconds, uploadVideo, VIDEO_TYPES, type UploadProgress } from "@/lib/upload";
import { cn, formatBytes } from "@/lib/utils";

/**
 * The article's video: shown on the website, uploaded to YouTube, or posted as a Facebook Reel.
 * Choosing a file uploads it at once; the article itself only changes when it is saved.
 */
export function VideoCard({
  video,
  videoId,
  readOnly,
  onChange,
}: {
  video: VideoOut | null;
  videoId: string | null;
  readOnly: boolean;
  onChange: (v: VideoOut | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const uploading = progress !== null;
  useEffect(() => {
    if (!uploading) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault(); // leaving would stop the upload
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (file.type && !VIDEO_TYPES.includes(file.type)) {
      setError("Choose an MP4 or MOV video file.");
      return;
    }
    const ctrl = new AbortController();
    abort.current = ctrl;
    setProgress({ fraction: 0, stage: "uploading" });
    try {
      const v = await uploadVideo(file, setProgress, ctrl.signal);
      onChange(v);
      toast.success("Video uploaded. Save the article to keep it.");
    } catch (e) {
      if ((e as { code?: string }).code === "aborted") toast.info("Upload cancelled");
      else setError(errorMessage(e));
    } finally {
      setProgress(null);
      abort.current = null;
      if (input.current) input.current.value = "";
    }
  };

  const pct = Math.round((progress?.fraction ?? 0) * 100);
  const vertical = !!(video?.width && video?.height && video.height > video.width);

  return (
    <Card>
      <CardHeader
        title="Video"
        icon={Clapperboard}
        description="Optional. Shown on your website, uploaded to YouTube, or posted as a Facebook Reel."
        actions={
          !readOnly && !uploading ? (
            <Button size="sm" variant="outline" onClick={() => input.current?.click()}>
              <Upload className="h-3.5 w-3.5" /> {videoId ? "Replace" : "Upload video"}
            </Button>
          ) : null
        }
      />
      <CardBody className="flex flex-col gap-3">
        <input
          ref={input}
          type="file"
          accept="video/mp4,video/quicktime,.mp4,.mov,.m4v"
          className="hidden"
          aria-label="Video file"
          data-testid="video-file"
          onChange={(e) => void pick(e.target.files?.[0])}
        />
        {uploading ? (
          <div className="flex flex-col gap-1.5" role="status" aria-live="polite">
            <div className="flex items-center justify-between text-xs">
              <span className={cn(progress?.stage === "retrying" && "text-warning")}>
                {progress?.stage === "checking"
                  ? "Checking the video…"
                  : progress?.stage === "retrying"
                    ? `Connection lost. Retrying… ${pct}%`
                    : `Uploading… ${pct}%`}
              </span>
              <Button size="sm" variant="ghost" disabled={progress?.stage === "checking"} onClick={() => abort.current?.abort()}>
                Cancel
              </Button>
            </div>
            <div className="h-2 overflow-hidden rounded bg-surface-2">
              <div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
            </div>
            <p className="text-2xs text-muted">Keep this page open until the upload finishes. If the connection drops, it continues by itself.</p>
          </div>
        ) : null}
        {error ? (
          <InlineAlert tone="danger" title="The video was not uploaded">
            {error}
          </InlineAlert>
        ) : null}
        {videoId && video ? (
          <div className="flex flex-col gap-3 sm:flex-row">
            <video
              key={video.id}
              src={video.url}
              controls
              preload="metadata"
              className={vertical ? "aspect-[9/16] max-h-72 w-auto self-start rounded bg-black" : "aspect-video w-full rounded bg-black sm:w-64"}
            />
            <div className="flex min-w-0 flex-1 flex-col gap-2 text-xs text-muted">
              <div>
                <p className="truncate text-sm font-medium text-text">{video.title || video.original_filename || "Video"}</p>
                <p>
                  {formatSeconds(video.duration_seconds)}
                  {video.width && video.height ? ` · ${video.width}×${video.height}${vertical ? " (vertical)" : ""}` : ""} · {formatBytes(video.file_size)}
                </p>
              </div>
              <ul className="flex flex-col gap-1">
                <li className="flex items-start gap-1.5">
                  <FacebookIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {video.reel_problems?.length ? (
                    <span>Not a Reel: {video.reel_problems.join(" ")} It can still go out as a Facebook post.</span>
                  ) : (
                    <span>Can be posted as a Facebook Reel.</span>
                  )}
                </li>
                <li className="flex items-start gap-1.5">
                  <YouTubeIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{video.is_short ? "Appears on YouTube as a Short (vertical, up to 3 minutes)." : "Uploads to YouTube as a regular video."}</span>
                </li>
              </ul>
              {!readOnly ? (
                <div>
                  <Button size="sm" variant="ghost" onClick={() => onChange(null)}>
                    <X className="h-3.5 w-3.5" /> Remove video
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        ) : videoId ? (
          <p className="text-sm text-muted">Loading video…</p>
        ) : !uploading ? (
          <p className="text-sm text-muted">No video. MP4 or MOV; for a Facebook Reel use a vertical video of 3–90 seconds.</p>
        ) : null}
      </CardBody>
    </Card>
  );
}
