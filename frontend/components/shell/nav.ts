import {
  BarChart3,
  FileText,
  Images,
  LayoutDashboard,
  Layers,
  Microscope,
  Radio,
  Rss,
  ScrollText,
  Send,
  Settings,
  Target,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Key pressed after "g" to jump here. */
  key: string;
};

export const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, key: "o" },
  { href: "/live", label: "Live News", icon: Radio, key: "l" },
  { href: "/headlines", label: "My Headlines", icon: Target, key: "h" },
  { href: "/research", label: "Research Workspace", icon: Microscope, key: "r" },
  { href: "/clusters", label: "Story Clusters", icon: Layers, key: "c" },
  { href: "/sources", label: "Sources", icon: Rss, key: "s" },
  { href: "/images", label: "Image Library", icon: Images, key: "i" },
  { href: "/articles", label: "Articles", icon: FileText, key: "a" },
  { href: "/publishing", label: "Publishing", icon: Send, key: "p" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, key: "y" },
  { href: "/activity", label: "Activity Logs", icon: ScrollText, key: "v" },
  { href: "/settings", label: "Settings", icon: Settings, key: "," },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
