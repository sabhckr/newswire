/**
 * Video links → player addresses. Mirrors backend/app/articles/video.py: the server only keeps an
 * <iframe> whose address matches one of these players, so keep the two in step.
 */

const EMBED = [
  /^https:\/\/www\.youtube(?:-nocookie)?\.com\/embed\/[A-Za-z0-9_-]{6,20}(?:\?[A-Za-z0-9_=&;.%-]*)?$/,
  /^https:\/\/player\.vimeo\.com\/video\/\d{1,15}(?:\?[A-Za-z0-9_=&;.%-]*)?$/,
  /^https:\/\/www\.facebook\.com\/plugins\/video\.php\?[A-Za-z0-9_=&;.%/:+-]+$/,
  /^https:\/\/www\.dailymotion\.com\/embed\/video\/[A-Za-z0-9]{4,20}(?:\?[A-Za-z0-9_=&;.%-]*)?$/,
];

export function isEmbedUrl(url: string | null | undefined): boolean {
  return !!url && EMBED.some((re) => re.test(url));
}

const YT_ID = /^[A-Za-z0-9_-]{6,20}$/;

export function toEmbedUrl(input: string): string | null {
  const raw = input.trim();
  if (isEmbedUrl(raw)) return raw;
  let u: URL;
  try {
    u = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  const host = u.hostname.toLowerCase().replace(/^www\./, "").replace(/^m\./, "");
  const path = u.pathname;
  if (host === "youtube.com" || host === "youtube-nocookie.com" || host === "music.youtube.com") {
    const id = u.searchParams.get("v") ?? path.match(/^\/(?:shorts|live|embed)\/([A-Za-z0-9_-]{6,20})/)?.[1];
    return id && YT_ID.test(id) ? `https://www.youtube-nocookie.com/embed/${id}` : null;
  }
  if (host === "youtu.be") {
    const id = path.replace(/^\//, "").split("/")[0];
    return YT_ID.test(id) ? `https://www.youtube-nocookie.com/embed/${id}` : null;
  }
  if (host === "vimeo.com") {
    const id = path.match(/^\/(\d{1,15})/)?.[1];
    return id ? `https://player.vimeo.com/video/${id}` : null;
  }
  if (host === "dailymotion.com" || host === "dai.ly") {
    const id = path.match(/^\/(?:video\/)?([A-Za-z0-9]{4,20})/)?.[1];
    return id ? `https://www.dailymotion.com/embed/video/${id}` : null;
  }
  if (host === "facebook.com" || host === "fb.watch" || host === "web.facebook.com") {
    if (host === "fb.watch" || path.includes("/videos/") || path.startsWith("/watch") || path.startsWith("/reel/")) {
      let clean = `https://${host === "fb.watch" ? "fb.watch" : "www.facebook.com"}${path}`;
      if (u.search && path.startsWith("/watch")) clean += u.search;
      return `https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(clean)}&show_text=false`;
    }
  }
  return null;
}
