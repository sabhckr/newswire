"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RotateCcw, Save, Shield, Tag, Trash2, UserPlus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { MetaAppSettings } from "@/components/publishing/meta-app";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Switch } from "@/components/ui/switch";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { CategoryOut, SettingsMap, UserOut } from "@/lib/types";

/* ------------------------------------------------------------------ settings groups */

type FieldSpec = { label: string; hint?: string; min?: number; max?: number; step?: number; options?: string[] };

const GROUPS: { key: string; title: string; description: string; fields: Record<string, FieldSpec> }[] = [
  {
    key: "matching.weights",
    title: "Match scoring weights",
    description: "Points added per matched element. A story must reach a headline’s minimum score to be listed.",
    fields: {
      required_phrase: { label: "Required phrase", step: 1 },
      primary: { label: "Primary keyword", step: 1 },
      synonym: { label: "Synonym", step: 1 },
      secondary: { label: "Secondary keyword", step: 1 },
      geography: { label: "Geography match", step: 1 },
      category: { label: "Category match", step: 1 },
      selected_publisher: { label: "Selected publisher", step: 1 },
      recency_max: { label: "Recency bonus (max)", hint: "Decays linearly over the recency window", step: 1 },
      language: { label: "Language match", step: 1 },
      excluded_penalty: { label: "Excluded phrase penalty", hint: "Used only when excluded mode is “penalty”", step: 1 },
    },
  },
  {
    key: "matching.options",
    title: "Matching behaviour",
    description: "How terms are compared. Fuzzy matching catches spelling variants of words five letters or longer.",
    fields: {
      excluded_mode: { label: "Excluded phrases", options: ["reject", "penalty"] },
      fuzzy_enabled: { label: "Fuzzy matching" },
      fuzzy_threshold: { label: "Fuzzy similarity threshold", min: 60, max: 100, step: 1 },
      fuzzy_min_length: { label: "Fuzzy minimum word length", min: 3, max: 20, step: 1 },
      fuzzy_factor: { label: "Fuzzy weight factor", min: 0, max: 1, step: 0.05 },
      inflection_factor: { label: "Inflection weight factor", min: 0, max: 1, step: 0.05 },
      token_factor: { label: "Scattered-word factor", hint: "All words present but not as a phrase", min: 0, max: 1, step: 0.05 },
      body_factor: { label: "Summary-only factor", hint: "Match found in the summary but not the headline", min: 0, max: 1, step: 0.05 },
      recency_window_hours: { label: "Recency window (hours)", min: 1, max: 720, step: 1 },
      geo_min_confidence: { label: "Minimum location confidence", min: 0, max: 1, step: 0.05 },
      geo_include_publisher: { label: "Count publisher location as story location" },
      backfill_days: { label: "Backfill on create (days)", min: 0, max: 90, step: 1 },
      backfill_limit: { label: "Backfill item limit", min: 0, max: 50000, step: 100 },
    },
  },
  {
    key: "dedup",
    title: "Duplicate detection",
    description: "Exact URL and canonical duplicates are always merged; these thresholds govern near-duplicate flagging.",
    fields: {
      headline_threshold: { label: "Headline similarity threshold", min: 50, max: 100, step: 1 },
      headline_window_hours: { label: "Headline comparison window (hours)", min: 1, max: 720, step: 1 },
      min_headline_tokens: { label: "Minimum headline words", min: 1, max: 20, step: 1 },
      content_hash_window_days: { label: "Content hash window (days)", min: 1, max: 365, step: 1 },
    },
  },
  {
    key: "clustering",
    title: "Story clustering",
    description: "Groups reports of the same event across publishers. Editor merges, splits and locks always win.",
    fields: {
      enabled: { label: "Clustering enabled" },
      window_hours: { label: "Clustering window (hours)", min: 1, max: 720, step: 1 },
      headline_threshold: { label: "Headline similarity threshold", min: 50, max: 100, step: 1 },
      min_shared_title_tokens: { label: "Minimum shared headline words", min: 1, max: 10, step: 1 },
      keyword_jaccard: { label: "Keyword overlap (Jaccard)", min: 0.05, max: 1, step: 0.01 },
      min_shared_tokens: { label: "Minimum shared keywords", min: 1, max: 20, step: 1 },
      related_headline_floor: { label: "Headline floor for keyword clustering", min: 0, max: 100, step: 1 },
    },
  },
  {
    key: "collection",
    title: "Collection",
    description: "Per-source polling intervals are set on each source. Fetch logs older than the retention are deleted.",
    fields: {
      enabled: { label: "Collection enabled" },
      fetch_log_retention_days: { label: "Fetch log retention (days)", min: 1, max: 3650, step: 1 },
    },
  },
  {
    key: "backups",
    title: "Automatic backups",
    description: "The worker writes a verified archive (database, configuration and images) to storage/backups.",
    fields: {
      enabled: { label: "Scheduled backups" },
      interval_hours: { label: "Interval (hours)", min: 1, max: 720, step: 1 },
      retention_count: { label: "Keep the newest", hint: "Older completed backups are deleted", min: 1, max: 365, step: 1 },
      include_images: { label: "Include image files" },
    },
  },
];

function SettingsGroup({
  group,
  values,
  defaults,
  canWrite,
}: {
  group: (typeof GROUPS)[number];
  values: Record<string, unknown>;
  defaults: Record<string, unknown>;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>(values);
  useEffect(() => setDraft(values), [values]);
  const dirty = useMemo(() => Object.keys(group.fields).some((k) => draft[k] !== values[k]), [draft, values, group.fields]);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/settings/${group.key}`, { method: "PUT", body }),
    onSuccess: () => {
      toast.success(`${group.title} saved`);
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const changed = () => Object.fromEntries(Object.keys(group.fields).filter((k) => draft[k] !== values[k]).map((k) => [k, draft[k]]));

  return (
    <Card>
      <CardHeader
        title={group.title}
        description={group.description}
        actions={
          canWrite ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => setDraft({ ...values, ...defaults })} title="Fill in the default values (not saved until you save)">
                <RotateCcw className="h-3.5 w-3.5" /> Defaults
              </Button>
              <Button size="sm" variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(changed())}>
                <Save className="h-3.5 w-3.5" /> Save
              </Button>
            </>
          ) : null
        }
      />
      <CardBody className="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(group.fields).map(([k, spec]) => {
          const id = `${group.key}.${k}`;
          const v = draft[k];
          const def = defaults[k];
          if (typeof def === "boolean") {
            return (
              <div key={k} className="flex flex-col gap-1">
                <Switch id={id} checked={!!v} disabled={!canWrite} onCheckedChange={(c) => setDraft((d) => ({ ...d, [k]: c }))} label={spec.label} />
                {spec.hint ? <p className="text-xs text-muted">{spec.hint}</p> : null}
              </div>
            );
          }
          if (spec.options) {
            return (
              <Field key={k} label={spec.label} htmlFor={id} hint={spec.hint}>
                <Select id={id} value={String(v)} disabled={!canWrite} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}>
                  {spec.options.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </Select>
              </Field>
            );
          }
          return (
            <Field key={k} label={spec.label} htmlFor={id} hint={spec.hint ?? (def !== undefined ? `Default ${String(def)}` : undefined)}>
              <Input
                id={id}
                type="number"
                inputMode="decimal"
                value={v === undefined || v === null ? "" : String(v)}
                min={spec.min}
                max={spec.max}
                step={spec.step ?? "any"}
                disabled={!canWrite}
                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value === "" ? "" : Number(e.target.value) }))}
                className="tabular"
              />
            </Field>
          );
        })}
      </CardBody>
    </Card>
  );
}

function ConfigTab({ keys }: { keys: string[] }) {
  const { can } = useSession();
  const res = useQuery({ queryKey: ["settings"], queryFn: () => api<{ values: SettingsMap; defaults: SettingsMap }>("/settings") });
  if (res.isLoading) return <SkeletonRows />;
  if (res.isError) return <ErrorState error={res.error} onRetry={() => res.refetch()} />;
  const canWrite = can(PERMS.settingsWrite);
  return (
    <div className="space-y-4">
      {!canWrite ? <InlineAlert tone="info">Only administrators can change these settings.</InlineAlert> : null}
      {GROUPS.filter((g) => keys.includes(g.key)).map((g) => (
        <SettingsGroup key={g.key} group={g} values={res.data!.values[g.key] ?? {}} defaults={res.data!.defaults[g.key] ?? {}} canWrite={canWrite} />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ profile */

function ProfileTab() {
  const { session, refresh } = useSession();
  const { theme, setTheme } = useTheme();
  const [name, setName] = useState(session.user.display_name);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const saveProfile = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/auth/me", { method: "PATCH", body }),
    onSuccess: () => {
      toast.success("Profile saved");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const changePw = useMutation({
    mutationFn: () => api<{ other_sessions_revoked: number }>("/auth/me/password", { body: { current_password: pw.current, new_password: pw.next } }),
    onSuccess: (r) => {
      toast.success(`Password changed${r.other_sessions_revoked ? `; ${r.other_sessions_revoked} other session(s) signed out` : ""}`);
      setPw({ current: "", next: "", confirm: "" });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const pwError = pw.next && pw.next.length < 10 ? "Use at least 10 characters" : pw.confirm && pw.confirm !== pw.next ? "Passwords do not match" : undefined;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Profile" description={`${session.user.email} · ${session.user.role_names.join(", ")}`} />
        <CardBody className="space-y-4">
          <Field label="Display name" htmlFor="display_name">
            <Input id="display_name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          </Field>
          <Field label="Theme" htmlFor="theme" hint="Saved to your account and applied on every device you sign in from.">
            <Select
              id="theme"
              value={theme ?? "system"}
              onChange={(e) => {
                setTheme(e.target.value);
                saveProfile.mutate({ theme_preference: e.target.value });
              }}
            >
              <option value="system">Match the operating system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </Select>
          </Field>
          <Button variant="primary" disabled={!name.trim() || name === session.user.display_name} loading={saveProfile.isPending} onClick={() => saveProfile.mutate({ display_name: name.trim() })}>
            Save profile
          </Button>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Change password" description="Other signed-in sessions are signed out when you change it." icon={KeyRound} />
        <CardBody>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!pwError) changePw.mutate();
            }}
          >
            <input type="text" autoComplete="username" value={session.user.email} readOnly hidden />
            <Field label="Current password" htmlFor="pw-current">
              <Input id="pw-current" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            </Field>
            <Field label="New password" htmlFor="pw-new" hint="At least 10 characters">
              <Input id="pw-new" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
            </Field>
            <Field label="Repeat new password" htmlFor="pw-confirm" error={pwError}>
              <Input id="pw-confirm" type="password" autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} />
            </Field>
            <Button type="submit" variant="primary" disabled={!pw.current || !pw.next || !!pwError || pw.next !== pw.confirm} loading={changePw.isPending}>
              Change password
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ users */

type RoleInfo = { name: string; description: string; permissions: string[] };
const ROLE_NAMES = ["admin", "editor", "researcher", "viewer"] as const;

function UserDialog({ user, open, onOpenChange }: { user: UserOut | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ email: "", display_name: "", password: "", roles: ["viewer"] as string[] });
  useEffect(() => {
    if (open) setForm({ email: user?.email ?? "", display_name: user?.display_name ?? "", password: "", roles: user?.role_names ?? ["viewer"] });
  }, [open, user]);
  const save = useMutation({
    mutationFn: () =>
      user
        ? api(`/users/${user.id}`, { method: "PATCH", body: { display_name: form.display_name, roles: form.roles } })
        : api("/users", { body: form }),
    onSuccess: () => {
      toast.success(user ? "User updated" : "User created");
      qc.invalidateQueries({ queryKey: ["users"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggleRole = (r: string) => setForm((f) => ({ ...f, roles: f.roles.includes(r) ? f.roles.filter((x) => x !== r) : [...f.roles, r] }));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={user ? `Edit ${user.display_name}` : "Invite a user"}
        description={user ? user.email : "Share the password with them securely; they can change it after signing in."}
        footer={
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={save.isPending}
              disabled={!form.display_name.trim() || !form.roles.length || (!user && (!form.email || form.password.length < 10))}
              onClick={() => save.mutate()}
            >
              {user ? "Save" : "Create user"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {!user ? (
            <Field label="Email" htmlFor="u-email" required>
              <Input id="u-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
          ) : null}
          <Field label="Display name" htmlFor="u-name" required>
            <Input id="u-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </Field>
          {!user ? (
            <Field label="Initial password" htmlFor="u-pw" hint="At least 10 characters" required>
              <Input id="u-pw" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </Field>
          ) : null}
          <fieldset>
            <legend className="mb-1 text-xs font-medium">Roles</legend>
            <div className="flex flex-wrap gap-4">
              {ROLE_NAMES.map((r) => (
                <Checkbox key={r} label={r} checked={form.roles.includes(r)} onChange={() => toggleRole(r)} />
              ))}
            </div>
          </fieldset>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function UsersTab() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { session } = useSession();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<UserOut[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<RoleInfo[]>("/users/roles") });
  const [editing, setEditing] = useState<UserOut | null>(null);
  const [open, setOpen] = useState(false);
  const [resetFor, setResetFor] = useState<UserOut | null>(null);
  const [newPw, setNewPw] = useState("");
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api(`/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reset = useMutation({
    mutationFn: () => api(`/users/${resetFor!.id}/reset-password`, { body: { password: newPw } }),
    onSuccess: () => {
      toast.success("Password reset; their sessions were signed out");
      setResetFor(null);
      setNewPw("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Users"
          description="Everyone with access to this newsroom"
          actions={
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                setEditing(null);
                setOpen(true);
              }}
            >
              <UserPlus className="h-3.5 w-3.5" /> Invite user
            </Button>
          }
        />
        {users.isLoading ? (
          <SkeletonRows rows={3} />
        ) : users.isError ? (
          <ErrorState error={users.error} onRetry={() => users.refetch()} />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Roles</TH>
                <TH>State</TH>
                <TH>Last sign-in</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {users.data!.map((u) => (
                <TR key={u.id}>
                  <TD>
                    <span className="block font-medium">{u.display_name}</span>
                    <span className="block text-xs text-muted">{u.email}</span>
                  </TD>
                  <TD>
                    <div className="flex flex-wrap gap-1">
                      {u.role_names.map((r) => (
                        <Badge key={r} tone={r === "admin" ? "accent" : "neutral"} icon={r === "admin" ? Shield : undefined}>
                          {r}
                        </Badge>
                      ))}
                    </div>
                  </TD>
                  <TD>
                    <StatusBadge status={u.is_active ? "active" : "disabled"} />
                  </TD>
                  <TD className="text-muted">
                    <Time value={u.last_login_at} />
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(u);
                          setOpen(true);
                        }}
                      >
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setResetFor(u)}>
                        Reset password
                      </Button>
                      {u.id !== session.user.id ? (
                        <Button
                          size="sm"
                          variant={u.is_active ? "danger-outline" : "outline"}
                          onClick={async () => {
                            if (
                              !u.is_active ||
                              (await confirm({
                                title: `Deactivate ${u.display_name}?`,
                                body: "They are signed out immediately and cannot sign in until reactivated. Their work and audit history are kept.",
                                confirmLabel: "Deactivate",
                                destructive: true,
                              }))
                            )
                              patch.mutate({ id: u.id, body: { is_active: !u.is_active } });
                          }}
                        >
                          {u.is_active ? "Deactivate" : "Reactivate"}
                        </Button>
                      ) : null}
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader title="Roles" description="Permissions are fixed per role; give a person several roles if needed." icon={Shield} />
        <CardBody className="grid gap-3 md:grid-cols-2">
          {roles.data?.map((r) => (
            <div key={r.name} className="rounded border border-border p-3">
              <p className="text-sm font-semibold capitalize">{r.name}</p>
              <p className="text-xs text-muted">{r.description}</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {r.permissions.map((p) => (
                  <code key={p} className="rounded bg-surface-2 px-1 font-mono text-2xs">
                    {p}
                  </code>
                ))}
              </div>
            </div>
          ))}
        </CardBody>
      </Card>

      <UserDialog user={editing} open={open} onOpenChange={setOpen} />
      <Dialog open={!!resetFor} onOpenChange={(o) => !o && setResetFor(null)}>
        <DialogContent
          title={`Reset password for ${resetFor?.display_name ?? ""}`}
          description="Their existing sessions are signed out."
          footer={
            <>
              <Button variant="outline" onClick={() => setResetFor(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={newPw.length < 10} loading={reset.isPending} onClick={() => reset.mutate()}>
                Set password
              </Button>
            </>
          }
        >
          <Field label="New password" htmlFor="reset-pw" hint="At least 10 characters">
            <Input id="reset-pw" type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
          </Field>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ------------------------------------------------------------------ categories */

function CategoriesTab() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const [name, setName] = useState("");
  const cats = useQuery({ queryKey: ["categories"], queryFn: () => api<CategoryOut[]>("/categories") });
  const create = useMutation({
    mutationFn: () => api("/categories", { body: { name: name.trim() } }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/categories/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const canWrite = can(PERMS.sourcesWrite);
  return (
    <Card>
      <CardHeader title="Categories" description="Used to label sources and headlines and to group analytics." icon={Tag} />
      {canWrite ? (
        <form
          className="flex gap-2 border-b border-border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <Input placeholder="New category name" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs" aria-label="New category name" />
          <Button type="submit" variant="primary" disabled={!name.trim()} loading={create.isPending}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </form>
      ) : null}
      {cats.isLoading ? (
        <SkeletonRows rows={4} />
      ) : !cats.data?.length ? (
        <EmptyState icon={Tag} title="No categories" />
      ) : (
        <ul className="grid gap-px sm:grid-cols-2 lg:grid-cols-3">
          {cats.data.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2">
              <span className="text-sm">{c.name}</span>
              {canWrite ? (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={`Delete ${c.name}`}
                  onClick={async () => {
                    if (await confirm({ title: `Delete “${c.name}”?`, body: "Sources and headlines keep working; they just lose this label.", confirmLabel: "Delete", destructive: true }))
                      remove.mutate(c.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ page */

export default function SettingsPage() {
  const { can } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const tabs = [
    { id: "profile", label: "Profile & theme", show: true },
    { id: "scoring", label: "Matching & scoring", show: true },
    { id: "pipeline", label: "Dedup & clustering", show: true },
    { id: "collection", label: "Collection & backups", show: true },
    { id: "categories", label: "Categories", show: true },
    { id: "integrations", label: "Facebook & Instagram", show: true },
    { id: "users", label: "Users & roles", show: can(PERMS.usersManage) },
  ].filter((t) => t.show);
  const tab = tabs.find((t) => t.id === params.get("tab"))?.id ?? "profile";
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Settings" description="Your profile, the newsroom’s matching rules, collection and backups, and who has access." />
      <Tabs value={tab} onValueChange={(v) => router.replace(`/settings?tab=${v}`)}>
        <TabsList className="mb-4">
          {tabs.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="profile">
          <ProfileTab />
        </TabsContent>
        <TabsContent value="scoring">
          <ConfigTab keys={["matching.weights", "matching.options"]} />
        </TabsContent>
        <TabsContent value="pipeline">
          <ConfigTab keys={["dedup", "clustering"]} />
        </TabsContent>
        <TabsContent value="collection">
          <ConfigTab keys={["collection", "backups"]} />
        </TabsContent>
        <TabsContent value="categories">
          <CategoriesTab />
        </TabsContent>
        <TabsContent value="integrations">
          <MetaAppSettings canManage={can(PERMS.publishingManage)} />
        </TabsContent>
        {can(PERMS.usersManage) ? (
          <TabsContent value="users">
            <UsersTab />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  );
}
