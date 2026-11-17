import { cn } from "@/lib/utils";

export interface BreakdownLine {
  kind: string;
  label: string;
  points: number;
  detail: string;
}

const KIND_LABEL: Record<string, string> = {
  required: "Required",
  primary: "Primary",
  synonym: "Synonym",
  secondary: "Secondary",
  excluded: "Excluded",
  geography: "Place",
  category: "Category",
  publisher: "Publisher",
  selected_publisher: "Publisher",
  recency: "Recency",
  language: "Language",
};

export function asBreakdown(raw: unknown): BreakdownLine[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r): r is BreakdownLine => !!r && typeof r === "object" && "points" in r && "label" in r);
}

function signed(n: number) {
  const r = Math.round(n * 10) / 10;
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)}`;
}

/** Itemised scoring: why a report matched a headline. */
export function ScoreBreakdown({ lines, score, minScore, className }: { lines: BreakdownLine[]; score?: number; minScore?: number; className?: string }) {
  if (!lines.length) return <p className={cn("text-xs text-muted", className)}>No scoring detail stored for this match.</p>;
  return (
    <table className={cn("w-full text-xs", className)}>
      <caption className="sr-only">Score breakdown</caption>
      <tbody>
        {lines.map((l, i) => (
          <tr key={i} className="border-b border-border/60 last:border-0">
            <td className="whitespace-nowrap py-1 pr-2 text-muted">{KIND_LABEL[l.kind] ?? l.kind}</td>
            <td className="py-1 pr-2">
              <span className="font-medium">{l.label}</span>
              {l.detail ? <span className="text-muted"> — {l.detail}</span> : null}
            </td>
            <td className={cn("tabular whitespace-nowrap py-1 text-right font-medium", l.points < 0 ? "text-danger" : "text-success")}>{signed(l.points)}</td>
          </tr>
        ))}
        {score !== undefined ? (
          <tr>
            <td className="pt-1.5 text-muted" colSpan={2}>
              Total{minScore !== undefined ? <> (threshold {Math.round(minScore)})</> : null}
            </td>
            <td className="tabular pt-1.5 text-right font-semibold">{Math.round(score)}</td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

/** Compact score chip; the number is always shown so colour is never the only signal. */
export function ScorePill({ score, minScore }: { score: number; minScore?: number }) {
  const strong = minScore !== undefined ? score >= minScore * 2 : score >= 50;
  return (
    <span
      className={cn(
        "tabular inline-flex min-w-[2.5rem] justify-center rounded px-1.5 py-0.5 text-xs font-semibold",
        strong ? "bg-success/10 text-success" : "bg-info/10 text-info",
      )}
      title={`Relevance score ${Math.round(score)}${minScore !== undefined ? ` (threshold ${Math.round(minScore)})` : ""}`}
    >
      {Math.round(score)}
    </span>
  );
}
