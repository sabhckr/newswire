import { FileText, Images, LayoutDashboard, Radio, Rss, Send, Settings } from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Key pressed after "g" to jump here. */
  key: string;
};

/** The whole app, in the order the work happens: read the news, write, publish. */
export const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, key: "o" },
  { href: "/live", label: "Live News", icon: Radio, key: "l" },
  { href: "/articles", label: "Articles", icon: FileText, key: "a" },
  { href: "/images", label: "Image Library", icon: Images, key: "i" },
  { href: "/publishing", label: "Publishing", icon: Send, key: "p" },
  { href: "/sources", label: "Sources", icon: Rss, key: "s" },
  { href: "/settings", label: "Settings", icon: Settings, key: "," },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
