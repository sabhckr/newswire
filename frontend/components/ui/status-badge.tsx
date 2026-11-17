import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock,
  Eye,
  EyeOff,
  FileEdit,
  Loader2,
  PauseCircle,
  Send,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import { Badge, type Tone } from "./badge";

type Spec = { tone: Tone; icon: React.ComponentType<{ className?: string }>; label?: string };

const MAP: Record<string, Spec> = {
  // articles
  draft: { tone: "neutral", icon: FileEdit, label: "Draft" },
  in_review: { tone: "warning", icon: Eye, label: "In review" },
  approved: { tone: "info", icon: ShieldCheck, label: "Approved" },
  scheduled: { tone: "accent", icon: Clock, label: "Scheduled" },
  published: { tone: "success", icon: CheckCircle2, label: "Published" },
  unpublished: { tone: "neutral", icon: CircleDashed, label: "Unpublished" },
  // remote state on Facebook / YouTube
  processing: { tone: "info", icon: Loader2, label: "Processing" },
  private: { tone: "warning", icon: EyeOff, label: "Private" },
  deleted: { tone: "neutral", icon: XCircle, label: "Deleted there" },
  archived: { tone: "neutral", icon: Archive, label: "Archived" },
  // assignments
  active: { tone: "success", icon: CircleDot, label: "Active" },
  paused: { tone: "neutral", icon: PauseCircle, label: "Paused" },
  // matches / images
  unreviewed: { tone: "info", icon: CircleDot, label: "Unreviewed" },
  verified: { tone: "success", icon: CheckCircle2, label: "Verified" },
  rejected: { tone: "danger", icon: XCircle, label: "Rejected" },
  pending: { tone: "warning", icon: Clock, label: "Pending" },
  // jobs / fetches
  running: { tone: "info", icon: Loader2, label: "Running" },
  succeeded: { tone: "success", icon: CheckCircle2, label: "Succeeded" },
  success: { tone: "success", icon: CheckCircle2, label: "Success" },
  ok: { tone: "success", icon: CheckCircle2, label: "OK" },
  completed: { tone: "success", icon: CheckCircle2, label: "Completed" },
  not_modified: { tone: "neutral", icon: CheckCircle2, label: "Not modified" },
  partial: { tone: "warning", icon: AlertTriangle, label: "Partial" },
  failed: { tone: "danger", icon: XCircle, label: "Failed" },
  error: { tone: "danger", icon: XCircle, label: "Error" },
  blocked: { tone: "danger", icon: XCircle, label: "Blocked" },
  cancelled: { tone: "neutral", icon: XCircle, label: "Cancelled" },
  // health
  healthy: { tone: "success", icon: CheckCircle2, label: "Healthy" },
  degraded: { tone: "warning", icon: AlertTriangle, label: "Degraded" },
  failing: { tone: "danger", icon: XCircle, label: "Failing" },
  disabled: { tone: "neutral", icon: PauseCircle, label: "Disabled" },
  new: { tone: "info", icon: CircleDot, label: "New" },
  open: { tone: "info", icon: CircleDot, label: "Open" },
  in_progress: { tone: "warning", icon: Loader2, label: "In progress" },
  done: { tone: "success", icon: CheckCircle2, label: "Done" },
};

export function statusLabel(status: string): string {
  return MAP[status]?.label ?? status.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function StatusBadge({ status, label, className }: { status: string | null | undefined; label?: string; className?: string }) {
  if (!status) return null;
  const spec = MAP[status] ?? { tone: "neutral" as Tone, icon: CircleDot };
  return (
    <Badge tone={spec.tone} icon={spec.icon} className={className}>
      {label ?? spec.label ?? statusLabel(status)}
    </Badge>
  );
}

const PRIORITY: Record<string, Tone> = { low: "neutral", normal: "info", high: "warning", urgent: "danger" };

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge tone={PRIORITY[priority] ?? "neutral"} icon={priority === "urgent" ? AlertTriangle : undefined}>
      {priority.charAt(0).toUpperCase() + priority.slice(1)}
    </Badge>
  );
}

export function SendBadge() {
  return <Send className="h-3 w-3" aria-hidden />;
}
