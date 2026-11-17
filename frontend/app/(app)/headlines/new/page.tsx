"use client";

import { AssignmentForm } from "@/components/assignments/assignment-form";
import { PageHeader } from "@/components/ui/page";

export default function NewHeadlinePage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="New headline"
        description="Describe the story, add keywords and places, and watch the preview to tune what matches. Saving scores the last 7 days of collected reports."
      />
      <AssignmentForm />
    </div>
  );
}
