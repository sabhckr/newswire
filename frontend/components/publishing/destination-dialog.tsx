"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, LogIn, Wand2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { DestinationIn, DestinationOut, DestinationUpdateIn } from "@/lib/types";

import { FacebookIcon, InstagramIcon, META_KINDS, YouTubeIcon, isSocial } from "./kinds";
import { useMetaApp } from "./meta-app";

type Kind = "website" | "wordpress" | "webhook" | "facebook" | "instagram" | "youtube";
type BodyFormat = "auto" | "html" | "text";
type Form = {
  kind: Kind;
  name: string;
  base_url: string;
  username: string;
  secret: string;
  client_id: string;
  login_config_id: string;
  body_format: BodyFormat;
  is_active: boolean;
  is_commercial: boolean;
  auto_publish: boolean;
};

function initial(d: DestinationOut | null): Form {
  return {
    kind: (d ? d.kind : "website") as Kind,
    name: d?.name ?? "",
    base_url: d?.base_url ?? "",
    username: d?.username ?? "",
    secret: "",
    client_id: d?.connection?.client_id ?? "",
    login_config_id: d?.connection?.login_config_id ?? "",
    body_format: (d?.website?.body_format ?? "auto") as BodyFormat,
    is_active: d?.is_active ?? true,
    is_commercial: d?.is_commercial ?? true,
    auto_publish: d?.auto_publish ?? false,
  };
}

/** Start the Facebook / Google sign-in for a saved destination: the browser leaves for their page. */
export async function startConnect(d: DestinationOut) {
  const r = await api<{ authorize_url: string }>(`/publishing/destinations/${d.id}/connect`, { method: "POST" });
  window.location.assign(r.authorize_url);
}

export function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex gap-2">
      <Input readOnly value={value} aria-label={label} className="font-mono text-xs" onFocus={(e) => e.currentTarget.select()} />
      <Button
        type="button"
        variant="outline"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          } catch {
            toast.error("Copy failed; select the address and copy it by hand.");
          }
        }}
      >
        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function SetupSteps({ kind, redirectUri }: { kind: string; redirectUri: string }) {
  return (
    <details className="rounded-lg border border-border p-3 text-xs text-muted" open>
      <summary className="cursor-pointer text-sm font-medium text-text">How to get these (one time, about 10 minutes)</summary>
      {kind !== "youtube" ? (
        <ol className="mt-2 list-decimal space-y-1.5 pl-5">
          <li>
            Open{" "}
            <a href="https://developers.facebook.com/apps/" target="_blank" rel="noopener noreferrer" className="text-accent underline">
              developers.facebook.com/apps
            </a>{" "}
            and create an app. Choose the use case for managing your Page (posts and Reels).
          </li>
          <li>Under App settings → Basic, copy the <b>App ID</b> and <b>App secret</b> into the fields above.</li>
          <li>
            Under Facebook Login → Settings, add this address to <b>Valid OAuth Redirect URIs</b>:
            <div className="mt-1">
              <CopyField value={redirectUri} label="Redirect address for Facebook" />
            </div>
          </li>
          <li>Save here, then click <b>Connect</b>, sign in with an account that manages the Page, and allow every permission.</li>
        </ol>
      ) : (
        <ol className="mt-2 list-decimal space-y-1.5 pl-5">
          <li>
            In{" "}
            <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noopener noreferrer" className="text-accent underline">
              Google Cloud Console
            </a>
            , create a project and enable <b>YouTube Data API v3</b>.
          </li>
          <li>
            Set up the OAuth consent screen (External). Then <b>publish the app</b>: while it is in Testing, Google ends the connection after 7
            days.
          </li>
          <li>
            Credentials → Create credentials → OAuth client ID → <b>Web application</b>. Add this <b>Authorized redirect URI</b>:
            <div className="mt-1">
              <CopyField value={redirectUri} label="Redirect address for Google" />
            </div>
          </li>
          <li>Copy the <b>Client ID</b> and <b>Client secret</b> above, save, then click <b>Connect</b> and pick the channel.</li>
          <li>Until Google audits the project, YouTube may keep uploaded videos private. NEWSWIRE tells you when that happens.</li>
        </ol>
      )}
    </details>
  );
}

function randomSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Create or edit a destination. Secrets are write-only: the API only reports whether one is stored. */
export function DestinationDialog({
  open,
  onOpenChange,
  destination,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  destination: DestinationOut | null;
  onSaved?: (d: DestinationOut) => void;
}) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => initial(destination));
  const [errors, setErrors] = useState<Partial<Record<keyof Form, string>>>({});
  useEffect(() => {
    if (open) {
      setF(initial(destination));
      setErrors({});
    }
  }, [open, destination]);

  const editing = !!destination;
  const builtin = destination?.kind === "builtin";
  const kind: string = destination?.kind ?? f.kind;
  const social = isSocial(kind);
  const meta = META_KINDS.has(kind);
  const site = kind === "website";
  const metaApp = useMetaApp(open && meta);
  // Facebook and Instagram sign in with the Meta app from Settings; an older Facebook destination
  // may still carry an app of its own, which stays editable here.
  const ownApp = meta && editing && !!destination?.connection && !destination.connection.shared_app;
  const sharedApp = meta && !ownApp;
  const sharedReady = !!metaApp.data?.configured;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const info = useQuery({
    queryKey: ["oauth-info"],
    queryFn: () => api<{ redirect_uri: string }>("/publishing/oauth/info"),
    enabled: open,
    staleTime: Infinity,
  });
  const redirectUri = destination?.connection?.redirect_uri ?? info.data?.redirect_uri ?? "";

  const save = useMutation({
    mutationFn: async () => {
      if (!destination && site) {
        const body: DestinationIn = {
          name: f.name.trim(),
          kind: "website",
          base_url: f.base_url.trim(),
          username: f.username.trim(),
          secret: f.secret,
          body_format: f.body_format,
          is_active: f.is_active,
          is_commercial: f.is_commercial,
          auto_publish: f.auto_publish,
        };
        return api<DestinationOut>("/publishing/destinations", { body });
      }
      if (!destination && social) {
        const body: DestinationIn = {
          name: f.name.trim(),
          kind: f.kind,
          client_id: sharedApp ? null : f.client_id.trim(),
          secret: sharedApp ? null : f.secret.trim(),
          login_config_id: sharedApp ? null : f.login_config_id.trim() || null,
          is_active: f.is_active,
          is_commercial: f.is_commercial,
          auto_publish: f.auto_publish,
        };
        return api<DestinationOut>("/publishing/destinations", { body });
      }
      if (!destination) {
        const body: DestinationIn = {
          name: f.name.trim(),
          kind: f.kind,
          base_url: f.base_url.trim(),
          username: f.kind === "wordpress" ? f.username.trim() || null : null,
          secret: f.secret.trim() || null,
          is_active: f.is_active,
          is_commercial: f.is_commercial,
          auto_publish: f.auto_publish,
        };
        return api<DestinationOut>("/publishing/destinations", { body });
      }
      const body: DestinationUpdateIn = {};
      if (f.name.trim() !== destination.name) body.name = f.name.trim();
      if (f.is_active !== destination.is_active) body.is_active = f.is_active;
      if (f.is_commercial !== destination.is_commercial) body.is_commercial = f.is_commercial;
      if (f.auto_publish !== destination.auto_publish) body.auto_publish = f.auto_publish;
      if (site) {
        if (f.base_url.trim() !== (destination.base_url ?? "")) body.base_url = f.base_url.trim();
        if (f.username.trim() && f.username.trim() !== (destination.username ?? "")) body.username = f.username.trim();
        if (f.secret) body.secret = f.secret;
        if (f.body_format !== (destination.website?.body_format ?? "auto")) body.body_format = f.body_format;
      } else if (sharedApp) {
        // Signed in through the shared Meta app: only the name and switches change here.
      } else if (social) {
        if (f.client_id.trim() && f.client_id.trim() !== (destination.connection?.client_id ?? "")) body.client_id = f.client_id.trim();
        if (f.login_config_id.trim() !== (destination.connection?.login_config_id ?? "")) body.login_config_id = f.login_config_id.trim();
        if (f.secret.trim()) body.secret = f.secret.trim();
      } else if (!builtin) {
        if (f.base_url.trim() !== (destination.base_url ?? "")) body.base_url = f.base_url.trim();
        if (kind === "wordpress" && f.username.trim() !== (destination.username ?? "")) body.username = f.username.trim() || null;
        if (f.secret.trim()) body.secret = f.secret.trim();
      }
      return api<DestinationOut>(`/publishing/destinations/${destination.id}`, { method: "PUT", body });
    },
    onSuccess: async (d) => {
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
      onOpenChange(false);
      onSaved?.(d);
      if (d.kind === "website") {
        const w = d.website;
        if (w?.platform === "wordpress") toast.success(`Signed in to ${d.name}. It is WordPress, so it is ready to publish.`);
        else if (w?.taught) toast.success(`Saved ${d.name}.`);
        else toast.success(`Signed in to ${d.name}. Now show NEWSWIRE the site's post form once (Teach).`);
        return;
      }
      if (isSocial(d.kind) && !d.connection?.connected) {
        toast.info(`Saved ${d.name}. Opening ${META_KINDS.has(d.kind) ? "Facebook" : "Google"} to sign in…`);
        try {
          await startConnect(d);
        } catch (e) {
          toast.error(errorMessage(e));
        }
        return;
      }
      toast.success(editing ? `Saved ${d.name}` : `Added ${d.name}. Test the connection before publishing.`);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "unsafe_url") setErrors({ base_url: e.message });
      else if (e instanceof ApiError && e.code === "website_login") setErrors({ secret: e.message });
      else if (e instanceof ApiError && e.code === "website_unreachable") setErrors({ base_url: e.message });
      else toast.error(errorMessage(e));
    },
  });

  const submit = () => {
    const errs: Partial<Record<keyof Form, string>> = {};
    if (!f.name.trim()) errs.name = "Give the destination a name";
    if (site) {
      let ok = false;
      try {
        const u = new URL(f.base_url.trim());
        ok = u.protocol === "https:" || u.protocol === "http:";
      } catch {
        ok = false;
      }
      if (!ok) errs.base_url = "Paste the full address of the admin login page, starting with https://";
      if (!f.username.trim()) errs.username = "Enter the admin email or username";
      if (!editing && !f.secret) errs.secret = "Enter the admin password";
    } else if (sharedApp) {
      if (!editing && !sharedReady) errs.client_id = "Set up the Meta app first (Settings → Facebook & Instagram)";
    } else if (social) {
      if (!f.client_id.trim()) errs.client_id = meta ? "Paste the App ID" : "Paste the Client ID";
      if (!editing && !f.secret.trim()) errs.secret = meta ? "Paste the App secret" : "Paste the Client secret";
    } else if (!builtin) {
      let ok = false;
      try {
        const u = new URL(f.base_url.trim());
        ok = u.protocol === "https:" || u.protocol === "http:";
      } catch {
        ok = false;
      }
      if (!ok) errs.base_url = "Enter a full URL starting with https://";
      if (kind === "wordpress" && !f.username.trim()) errs.username = "WordPress needs the username that owns the application password";
      if (kind === "wordpress" && !editing && !f.secret.trim()) errs.secret = "Paste a WordPress application password";
    }
    setErrors(errs);
    if (!Object.keys(errs).length) save.mutate();
  };

  const secretLabel = kind === "wordpress" ? "Application password" : meta ? "App secret" : kind === "youtube" ? "Client secret" : "Signing secret";
  const secretHint = editing && destination?.has_secret
    ? "A credential is stored and never shown again. Leave blank to keep it, or enter a new one to replace it."
    : kind === "wordpress"
      ? "Create one in WordPress under Users → Profile → Application Passwords. It is stored encrypted."
      : "Used to sign every request (HMAC-SHA256). Copy it to your receiving site now: it cannot be shown again.";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={editing ? `Edit ${destination?.name}` : "Add a publishing destination"}
        description={builtin ? "The built-in site serves the public API and RSS feed. Only its name and flags can change." : undefined}
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={save.isPending} onClick={submit}>
              {editing ? "Save" : site ? "Sign in and add" : social ? "Save and sign in" : "Add destination"}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {!editing ? (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1 text-xs font-medium">Type</legend>
              {(
                [
                  ["website", "Website (admin login)", "Any news website: sign in with its own admin login page, email and password. Saved, so publishing always works."],
                  ["facebook", "Facebook Page", "Sign in with Facebook and choose the Page. Posts and Reels."],
                  ["instagram", "Instagram account", "Sign in with Facebook and choose the Instagram account linked to your Page. Photo posts and Reels."],
                  ["youtube", "YouTube channel", "Uploads the article's video. Connect once with your Google account."],
                  ["wordpress", "WordPress (application password)", "For developers: a WordPress username and an application password you created yourself."],
                  ["webhook", "Website (webhook)", "For developers: POSTs signed JSON for each publish, update and unpublish."],
                ] as const
              ).map(([k, label, help]) => (
                <label key={k} className="flex cursor-pointer items-start gap-2 rounded border border-border p-2 text-sm has-[:checked]:border-accent">
                  <input type="radio" name="dest-kind" value={k} checked={f.kind === k} onChange={() => set("kind", k)} className="mt-1 accent-[rgb(var(--accent))]" />
                  <span>
                    <span className="inline-flex items-center gap-1.5 font-medium">
                      {k === "website" ? (
                        <LogIn className="h-3.5 w-3.5" />
                      ) : k === "facebook" ? (
                        <FacebookIcon className="h-3.5 w-3.5" />
                      ) : k === "instagram" ? (
                        <InstagramIcon className="h-3.5 w-3.5" />
                      ) : k === "youtube" ? (
                        <YouTubeIcon className="h-3.5 w-3.5" />
                      ) : null}
                      {label}
                    </span>
                    <span className="block text-xs text-muted">{help}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}

          <Field label="Name" htmlFor="dest-name" required error={errors.name}>
            <Input
              id="dest-name"
              value={f.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder={site ? "e.g. Newsfield" : kind === "facebook" ? "e.g. Our Facebook Page" : kind === "instagram" ? "e.g. Our Instagram" : kind === "youtube" ? "e.g. Our YouTube channel" : "e.g. Main WordPress site"}
              aria-invalid={!!errors.name}
            />
          </Field>

          {site ? (
            <>
              <Field
                label="Admin login page"
                htmlFor="dest-url"
                required
                error={errors.base_url}
                hint="Where you sign in to the website's admin panel, e.g. https://example.com/admin/login or https://example.com/wp-login.php"
              >
                <Input id="dest-url" type="url" value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://" aria-invalid={!!errors.base_url} />
              </Field>
              <Field label="Admin email or username" htmlFor="dest-user" required error={errors.username}>
                <Input id="dest-user" value={f.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={!!errors.username} />
              </Field>
              <Field
                label="Admin password"
                htmlFor="dest-secret"
                required={!editing}
                error={errors.secret}
                hint={
                  editing && destination?.has_secret
                    ? "Stored encrypted and never shown. Leave blank to keep it; NEWSWIRE signs in again if you change it."
                    : "Stored encrypted in NEWSWIRE on this computer. NEWSWIRE uses it to sign in whenever the website logs it out."
                }
              >
                <Input
                  id="dest-secret"
                  type="password"
                  value={f.secret}
                  onChange={(e) => set("secret", e.target.value)}
                  autoComplete="new-password"
                  placeholder={editing && destination?.has_secret ? "•••••••• (unchanged)" : ""}
                  aria-invalid={!!errors.secret}
                />
              </Field>
              {!editing ? (
                <InlineAlert tone="info" title="What happens next">
                  NEWSWIRE signs in once to check the details. A WordPress site is then ready straight away. For any other site, a browser
                  window opens and you click the title box, the text box and the Publish button once, so NEWSWIRE learns the form.
                </InlineAlert>
              ) : null}
              <details className="text-xs text-muted">
                <summary className="cursor-pointer">Advanced: how the article text is put in</summary>
                <Field label="Article text" htmlFor="dest-body-format" hint="Automatic works for most sites. Use plain text if the site shows HTML tags in posts.">
                  <Select id="dest-body-format" value={f.body_format} onChange={(e) => set("body_format", e.target.value as BodyFormat)}>
                    <option value="auto">Automatic</option>
                    <option value="html">Formatted (HTML)</option>
                    <option value="text">Plain text</option>
                  </Select>
                </Field>
              </details>
            </>
          ) : null}

          {sharedApp ? (
            editing ? (
              destination?.connection?.connected ? (
                <InlineAlert tone="success" title={`Connected: ${destination.connection.account_name ?? ""}`}>
                  Signed in with the Meta app from Settings. Use Reconnect on the card to sign in again or choose another{" "}
                  {kind === "instagram" ? "account" : "Page"}.
                </InlineAlert>
              ) : null
            ) : metaApp.isLoading ? null : sharedReady ? (
              <InlineAlert tone="info" title={kind === "instagram" ? "Sign in with Facebook, then choose the account" : "Sign in with Facebook, then choose the Page"}>
                {kind === "instagram"
                  ? "Instagram publishing goes through the Facebook Page the account is linked to. The Instagram account must be Professional (Business or Creator)."
                  : "Sign in with a Facebook account that manages the Page and allow every permission asked for."}
              </InlineAlert>
            ) : (
              <InlineAlert tone="warning" title="One-time setup needed">
                Facebook and Instagram need a Meta app, set up once by an administrator.{" "}
                <Link href="/settings?tab=integrations" className="font-medium text-accent underline" onClick={() => onOpenChange(false)}>
                  Open Settings → Facebook &amp; Instagram
                </Link>
                {errors.client_id ? <span className="mt-1 block text-danger">{errors.client_id}</span> : null}
              </InlineAlert>
            )
          ) : null}

          {social && !sharedApp ? (
            <>
              {editing && destination?.connection?.connected ? (
                <InlineAlert tone="success" title={`Connected: ${destination.connection.account_name ?? ""}`}>
                  Changing the {meta ? "App ID" : "Client ID"} disconnects it; you then connect again.
                </InlineAlert>
              ) : null}
              <Field label={meta ? "App ID" : "Client ID"} htmlFor="dest-client" required error={errors.client_id}>
                <Input
                  id="dest-client"
                  value={f.client_id}
                  onChange={(e) => set("client_id", e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono text-xs"
                  aria-invalid={!!errors.client_id}
                />
              </Field>
              <Field
                label={secretLabel}
                htmlFor="dest-secret"
                required={!editing}
                error={errors.secret}
                hint={editing && destination?.has_secret ? "Stored encrypted and never shown again. Leave blank to keep it." : "Stored encrypted on the server."}
              >
                <Input
                  id="dest-secret"
                  type="password"
                  value={f.secret}
                  onChange={(e) => set("secret", e.target.value)}
                  autoComplete="new-password"
                  spellCheck={false}
                  className="font-mono text-xs"
                  placeholder={editing && destination?.has_secret ? "•••••••• (unchanged)" : ""}
                  aria-invalid={!!errors.secret}
                />
              </Field>
              {kind === "facebook" ? (
                <details className="text-xs text-muted">
                  <summary className="cursor-pointer">Advanced: Facebook Login for Business</summary>
                  <Field label="Configuration ID (optional)" htmlFor="dest-config" hint="Only if your app uses Facebook Login for Business; otherwise leave empty.">
                    <Input id="dest-config" value={f.login_config_id} onChange={(e) => set("login_config_id", e.target.value)} className="font-mono text-xs" />
                  </Field>
                </details>
              ) : null}
              {redirectUri ? <SetupSteps kind={kind} redirectUri={redirectUri} /> : null}
            </>
          ) : null}

          {!builtin && !social && !site ? (
            <>
              <Field
                label={kind === "wordpress" ? "Site address" : "Endpoint URL"}
                htmlFor="dest-url"
                required
                error={errors.base_url}
                hint={kind === "wordpress" ? "The site root, e.g. https://news.example.com. NEWSWIRE calls /wp-json/wp/v2/posts." : "NEWSWIRE POSTs JSON here. Private network addresses are refused."}
              >
                <Input id="dest-url" type="url" value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://" aria-invalid={!!errors.base_url} />
              </Field>
              {kind === "wordpress" ? (
                <Field label="WordPress username" htmlFor="dest-user" required error={errors.username}>
                  <Input id="dest-user" value={f.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" aria-invalid={!!errors.username} />
                </Field>
              ) : null}
              <Field label={secretLabel} htmlFor="dest-secret" required={kind === "wordpress" && !editing} error={errors.secret} hint={secretHint}>
                <div className="flex gap-2">
                  <Input
                    id="dest-secret"
                    type={kind === "wordpress" ? "password" : "text"}
                    value={f.secret}
                    onChange={(e) => set("secret", e.target.value)}
                    autoComplete="new-password"
                    spellCheck={false}
                    className="font-mono text-xs"
                    placeholder={editing && destination?.has_secret ? "•••••••• (unchanged)" : ""}
                    aria-invalid={!!errors.secret}
                  />
                  {kind === "webhook" ? (
                    <Button type="button" variant="outline" onClick={() => set("secret", randomSecret())}>
                      <Wand2 className="h-4 w-4" /> Generate
                    </Button>
                  ) : null}
                </div>
              </Field>
              {editing && destination?.has_secret ? (
                <p className="-mt-1 inline-flex items-center gap-1.5 text-xs text-muted">
                  <KeyRound className="h-3.5 w-3.5" aria-hidden /> Credential stored
                </p>
              ) : null}
            </>
          ) : null}

          <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
            <div>
              <Switch id="dest-active" label="Active" checked={f.is_active} onCheckedChange={(v) => set("is_active", v)} />
              <p className="ml-10 text-xs text-muted">Inactive destinations are hidden from the publish panel.</p>
            </div>
            <div>
              <Switch id="dest-commercial" label="Commercial publication" checked={f.is_commercial} onCheckedChange={(v) => set("is_commercial", v)} />
              <p className="ml-10 text-xs text-muted">
                Leave on if the site carries ads, paywalls or sponsorship. Images under a NonCommercial (NC) licence are then blocked here.
                Turn off only for a genuinely non-commercial site.
              </p>
            </div>
            <div>
              <Switch id="dest-auto" label="Allow auto-publish" checked={f.auto_publish} onCheckedChange={(v) => set("auto_publish", v)} />
              <p className="ml-10 text-xs text-muted">
                Approved articles go out here without a manual publish, but only while the global auto-publish policy is also on.
              </p>
            </div>
            {f.auto_publish ? (
              <InlineAlert tone="warning" title="Articles may go live as soon as they are approved">
                If the global policy is on, approving an article publishes it here automatically, provided it passes every blocking check.
                Keep this off unless approvals are always final.
              </InlineAlert>
            ) : null}
          </div>
          <button type="submit" hidden aria-hidden tabIndex={-1} />
        </form>
      </DialogContent>
    </Dialog>
  );
}
