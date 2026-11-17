"use client";

import { useQuery } from "@tanstack/react-query";
import { Microscope } from "lucide-react";
import { useParams } from "next/navigation";

import { AssignmentForm } from "@/components/assignments/assignment-form";
import { LinkButton } from "@/components/assignments/link-button";
import { PageHeader } from "@/components/ui/page";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import type { AssignmentOut } from "@/lib/types";

export default function EditHeadlinePage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({
    queryKey: ["assignment", id],
    queryFn: ({ signal }) => api<AssignmentOut>(`/assignments/${id}`, { signal }),
    // The form copies the record once; do not swap it out underneath unsaved edits.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={q.data ? <>Edit “{q.data.headline}”</> : "Edit headline"}
        description={
          q.data ? (
            <span className="inline-flex items-center gap-2">
              <StatusBadge status={q.data.status} /> Saving re-scores the last 7 days of reports against the new rules.
            </span>
          ) : undefined
        }
        actions={
          <LinkButton href={`/research/${id}`}>
            <Microscope className="h-4 w-4" /> Research
          </LinkButton>
        }
      />
      {q.isLoading ? (
        <SkeletonRows rows={10} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <AssignmentForm key={q.data.id} initial={q.data} />
      ) : null}
    </div>
  );
}
