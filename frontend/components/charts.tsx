"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Chart colours come from CSS tokens so both themes work without re-rendering. */
export const series = (n: number) => `rgb(var(--chart-${((n - 1) % 6) + 1}))`;

const axis = { stroke: "rgb(var(--muted))", fontSize: 11, tickLine: false, axisLine: false } as const;

function TooltipBox({ active, payload, label, labelFormatter }: {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string }[];
  label?: string | number;
  labelFormatter?: (l: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-border bg-surface px-2 py-1.5 text-xs shadow-pop">
      <div className="mb-1 font-medium">{labelFormatter ? labelFormatter(String(label)) : label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} aria-hidden />
          <span className="text-muted">{p.name}</span>
          <span className="tabular ml-auto font-medium">{typeof p.value === "number" ? p.value.toLocaleString() : p.value}</span>
        </div>
      ))}
    </div>
  );
}

export function bucketLabel(b: string): string {
  // "2026-11-17T09:00" (hourly) or "2026-11-17" (daily)
  const hourly = b.includes("T");
  const d = new Date(hourly ? `${b}:00Z` : `${b}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return b;
  return hourly
    ? d.toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

type Row = Record<string, unknown>;

export function VolumeChart({
  data,
  x,
  bars,
  height = 220,
  stacked,
  kind = "area",
}: {
  data: object[];
  x: string;
  bars: { key: string; name: string }[];
  height?: number;
  stacked?: boolean;
  kind?: "area" | "bar";
}) {
  const Chart = kind === "area" ? AreaChart : BarChart;
  return (
    <div style={{ height }} role="img" aria-label={`Chart of ${bars.map((b) => b.name).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <Chart data={data as Row[]} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={x} {...axis} tickFormatter={(v) => bucketLabel(String(v))} minTickGap={24} />
          <YAxis {...axis} allowDecimals={false} width={44} />
          <Tooltip content={<TooltipBox labelFormatter={bucketLabel} />} cursor={{ fill: "rgb(var(--surface-2))" }} />
          {bars.map((b, i) =>
            kind === "area" ? (
              <Area
                key={b.key}
                type="monotone"
                dataKey={b.key}
                name={b.name}
                stroke={series(i + 1)}
                fill={series(i + 1)}
                fillOpacity={0.15}
                strokeWidth={1.5}
                stackId={stacked ? "s" : undefined}
                isAnimationActive={false}
              />
            ) : (
              <Bar key={b.key} dataKey={b.key} name={b.name} fill={series(i + 1)} stackId={stacked ? "s" : undefined} radius={[2, 2, 0, 0]} isAnimationActive={false} />
            ),
          )}
        </Chart>
      </ResponsiveContainer>
    </div>
  );
}

export function HBarChart({
  data,
  label,
  value,
  name,
  height,
}: {
  data: object[];
  label: string;
  value: string;
  name: string;
  height?: number;
}) {
  const h = height ?? Math.max(120, data.length * 26 + 20);
  return (
    <div style={{ height: h }} role="img" aria-label={`${name} by ${label}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data as Row[]} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" {...axis} allowDecimals={false} />
          <YAxis type="category" dataKey={label} {...axis} width={120} tick={{ fontSize: 11, fill: "rgb(var(--text))" }} />
          <Tooltip content={<TooltipBox />} cursor={{ fill: "rgb(var(--surface-2))" }} />
          <Bar dataKey={value} name={name} fill={series(1)} radius={[0, 2, 2, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
