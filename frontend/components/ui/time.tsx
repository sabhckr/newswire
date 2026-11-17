"use client";

import { useEffect, useState } from "react";

import { formatDateTime, relativeTime } from "@/lib/utils";

/** Relative time that refreshes every 30 s, with the absolute time on hover. */
export function Time({ value, className, absolute }: { value: string | null | undefined; className?: string; absolute?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (absolute) return;
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, [absolute]);
  if (!value) return <span className={className}>—</span>;
  return (
    <time dateTime={value} title={formatDateTime(value, { dateStyle: "full", timeStyle: "long" })} className={className}>
      {absolute ? formatDateTime(value) : relativeTime(value, now)}
    </time>
  );
}
