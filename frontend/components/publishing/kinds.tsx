import { Globe, LogIn, Webhook } from "lucide-react";

/** Facebook "f" mark (outline, to match the other line icons). */
export function FacebookIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z" />
    </svg>
  );
}

/** Instagram camera mark (outline). */
export function InstagramIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <rect x="2" y="2" width="20" height="20" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <path d="M17.5 6.5h.01" />
    </svg>
  );
}

/** YouTube play button (outline). */
export function YouTubeIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M2.5 17a24.12 24.12 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.56 49.56 0 0 1 16.2 0A2 2 0 0 1 21.5 7a24.12 24.12 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.55 49.55 0 0 1-16.2 0A2 2 0 0 1 2.5 17" />
      <path d="m10 15 5-3-5-3z" />
    </svg>
  );
}

export const KIND_LABEL: Record<string, string> = {
  builtin: "Built-in site",
  wordpress: "WordPress",
  webhook: "Website (webhook)",
  website: "Website (admin login)",
  facebook: "Facebook Page",
  instagram: "Instagram account",
  youtube: "YouTube channel",
};

export const KIND_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  builtin: Globe,
  wordpress: Globe,
  webhook: Webhook,
  website: LogIn,
  facebook: FacebookIcon,
  instagram: InstagramIcon,
  youtube: YouTubeIcon,
};

export const SOCIAL_KINDS = new Set(["facebook", "instagram", "youtube"]);
/** Facebook and Instagram: both sign in with the Meta app from Settings. */
export const META_KINDS = new Set(["facebook", "instagram"]);
export const isSocial = (kind: string | null | undefined) => SOCIAL_KINDS.has(kind ?? "");
