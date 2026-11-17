import type { SourceDraft } from "@/components/articles/sources-editor";
import type { ArticleIn, ArticleOut, ArticleSourceIn } from "@/lib/types";

/** Editable, non-body fields of an article as held by the editor form. */
export type Draft = {
  headline: string;
  subtitle: string;
  slug: string;
  excerpt: string;
  seo_title: string;
  seo_description: string;
  category_id: string;
  tags: string[];
  featured_image_id: string | null;
  featured_image_caption: string;
  video_id: string | null;
  sources: SourceDraft[];
};

export function toDraft(a: ArticleOut): Draft {
  return {
    headline: a.headline,
    subtitle: a.subtitle ?? "",
    slug: a.slug,
    excerpt: a.excerpt ?? "",
    seo_title: a.seo_title ?? "",
    seo_description: a.seo_description ?? "",
    category_id: a.category_id ?? "",
    tags: a.tags.map((t) => t.name),
    featured_image_id: a.featured_image_id,
    featured_image_caption: a.featured_image_caption ?? "",
    video_id: a.video_id ?? null,
    sources: a.sources.map((s) => ({
      news_item_id: s.news_item_id,
      url: s.url,
      title: s.title ?? "",
      publisher: s.publisher ?? "",
      note: s.note ?? "",
    })),
  };
}

const TEXT_FIELDS = ["subtitle", "excerpt", "seo_title", "seo_description", "featured_image_caption"] as const;

const orNull = (s: string) => (s.trim() ? s.trim() : null);
const sourceKey = (s: SourceDraft) => JSON.stringify([s.news_item_id, s.url, s.title.trim(), s.publisher.trim(), s.note.trim()]);
const tagKey = (t: string[]) => JSON.stringify(t.map((x) => x.trim().toLowerCase()));

/**
 * Only the fields that differ from the saved baseline. The backend treats tags and sources as changed
 * whenever they are present, so unchanged lists must be left out to avoid a pointless version bump.
 */
export function diffDraft(draft: Draft, base: Draft): ArticleIn {
  const out: ArticleIn = {};
  if (draft.headline.trim() !== base.headline.trim()) out.headline = draft.headline.trim();
  for (const f of TEXT_FIELDS) {
    if (orNull(draft[f]) !== orNull(base[f])) out[f] = orNull(draft[f]);
  }
  if (draft.slug.trim() && draft.slug.trim() !== base.slug) out.slug = draft.slug.trim();
  if ((draft.category_id || null) !== (base.category_id || null)) out.category_id = draft.category_id || null;
  if (draft.featured_image_id !== base.featured_image_id) out.featured_image_id = draft.featured_image_id;
  if (draft.video_id !== base.video_id) out.video_id = draft.video_id;
  if (tagKey(draft.tags) !== tagKey(base.tags)) out.tags = draft.tags.map((t) => t.trim()).filter(Boolean);
  if (draft.sources.map(sourceKey).join("\n") !== base.sources.map(sourceKey).join("\n")) {
    out.sources = draft.sources.map(
      (s): ArticleSourceIn => ({
        news_item_id: s.news_item_id,
        url: s.url,
        title: orNull(s.title),
        publisher: orNull(s.publisher),
        note: orNull(s.note),
      }),
    );
  }
  return out;
}

export function sameDraft(a: Draft, b: Draft): boolean {
  return Object.keys(diffDraft(a, b)).length === 0 && a.slug.trim() === b.slug.trim();
}

/** Fields whose edits send an approved article back to review (backend CONTENT_FIELDS). */
export const REVIEW_FIELDS = new Set(["headline", "subtitle", "body_html", "featured_image_id", "featured_image_caption", "video_id"]);
