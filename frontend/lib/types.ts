/** Response shapes that are built as plain dicts on the backend (not Pydantic models). */
export * from "./api-types";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CountedPage<T> extends Page<T> {
  status_counts: Record<string, number>;
}

export interface SessionPayload {
  user: import("./api-types").UserOut;
  permissions: string[];
  csrf_token: string;
}

export interface WorkerStatus {
  state: "running" | "stale" | "not_running";
  heartbeat_at: string | null;
  worker_id: string | null;
  age_seconds?: number;
  started_at?: string | null;
}

export interface Overview {
  as_of: string;
  day_start: string;
  status: {
    worker: WorkerStatus;
    collection_enabled: boolean;
    active_sources: number;
    failed_sources: number;
    last_collection_at: string | null;
    next_poll_at: string | null;
    last_cycle_status: string | null;
    pending_editorial_reviews: number;
  };
  metrics: {
    articles_collected_today: number;
    new_matches_today: number;
    active_assignments: number;
    distinct_publishers_today: number;
    pending_research_reviews: number;
    draft_articles: number;
    published_articles: number;
  };
}

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  is_read: boolean;
  created_at: string;
}

export interface SearchGroup {
  type: "news" | "assignments" | "clusters" | "articles" | "sources";
  label: string;
  items: { id: string; title: string; subtitle: string | null; href: string; at: string | null }[];
}

export interface LicenseWarning {
  level: "error" | "warning" | "info";
  code: string;
  message: string;
}

export interface PublicationCheck {
  level: "error" | "warning" | "info";
  code: string;
  message: string;
}

export interface ImageSearchResult {
  provider: "openverse" | "wikimedia";
  provider_id: string;
  title: string | null;
  creator: string | null;
  creator_url: string | null;
  source_page_url: string | null;
  original_url: string | null;
  thumbnail_url: string | null;
  width: number | null;
  height: number | null;
  mime: string | null;
  license_code: string;
  license_version: string;
  license_raw: string | null;
  license_url: string | null;
  attribution_text: string | null;
  repository: string | null;
  metadata: Record<string, unknown>;
  license_name: string;
  warnings: LicenseWarning[];
  blocked: boolean;
  saved_id: string | null;
  saved_status: string | null;
}

export interface ImageSearchResponse {
  provider: string;
  page: number;
  total: number | null;
  page_count: number | null;
  results: ImageSearchResult[];
}

export interface BackupRow {
  id: string;
  filename: string;
  kind: string;
  status: string;
  size_bytes: number | null;
  sha256: string | null;
  encrypted: boolean;
  includes: string[] | null;
  created_at: string;
  completed_at: string | null;
  verified_at: string | null;
  verify_status: string | null;
  error: string | null;
}

export interface JobRow {
  name: string;
  description: string | null;
  interval_seconds: number | null;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_duration_ms: number | null;
  next_run_at: string | null;
  run_count: number;
  failure_count: number;
}

export interface SystemStatus {
  version: string;
  environment: string;
  python: string;
  database: { dialect: string; version: string | null; alembic_revision: string | null };
  worker: WorkerStatus;
  jobs: JobRow[];
  fetches_24h: Record<string, number>;
  sources: { active: number; failing: number };
  storage: {
    images_bytes: number;
    backups_bytes: number;
    logs_bytes: number;
    disk_total_bytes: number;
    disk_free_bytes: number;
  };
  last_backup: { filename: string; created_at: string; size_bytes: number | null; verify_status: string | null } | null;
  config: Record<string, boolean | number | string>;
}

export interface FetchRow {
  id: string;
  source_id: string;
  source_name: string;
  publisher: string;
  trigger: string;
  status: string;
  http_status: number | null;
  started_at: string;
  duration_ms: number | null;
  items_found: number;
  items_new: number;
  items_duplicate: number;
  items_skipped: number;
  bytes_received: number | null;
  error_message: string | null;
}

export type SettingsMap = Record<string, Record<string, unknown>>;
