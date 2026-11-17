"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ClusterDetail } from "@/components/clusters/cluster-detail";

export default function ClusterPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  return (
    <div className="mx-auto max-w-4xl">
      <Link href={`/clusters?id=${id}`} className="mb-3 inline-flex items-center gap-1 rounded text-sm text-muted hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
        <ArrowLeft className="h-4 w-4" /> Story clusters
      </Link>
      <ClusterDetail id={id} onGone={() => router.push("/clusters")} />
    </div>
  );
}
