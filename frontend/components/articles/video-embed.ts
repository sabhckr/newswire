import { Node } from "@tiptap/react";

import { isEmbedUrl } from "@/lib/video";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    videoEmbed: {
      /** Insert a video player (YouTube, Facebook, Vimeo or Dailymotion embed address). */
      setVideoEmbed: (options: { src: string }) => ReturnType;
    };
  }
}

export const VIDEO_ALLOW = "accelerometer; encrypted-media; gyroscope; picture-in-picture; fullscreen";

function srcOf(el: HTMLElement): string | null {
  const frame = el.tagName === "IFRAME" ? el : el.querySelector("iframe");
  const src = frame?.getAttribute("src") ?? null;
  return isEmbedUrl(src) ? src : null;
}

/** A block video player. Saved as <div data-video-embed><iframe src="…"></iframe></div>. */
export const VideoEmbed = Node.create({
  name: "videoEmbed",
  group: "block",
  atom: true,
  draggable: true,
  selectable: true,

  addAttributes() {
    return { src: { default: null } };
  },

  parseHTML() {
    const getAttrs = (el: HTMLElement | string) => {
      if (typeof el === "string") return false;
      const src = srcOf(el);
      return src ? { src } : false;
    };
    return [
      { tag: "div[data-video-embed]", getAttrs },
      { tag: "iframe[src]", getAttrs },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "div",
      { "data-video-embed": "" },
      [
        "iframe",
        {
          src: HTMLAttributes.src,
          title: "Video",
          allow: VIDEO_ALLOW,
          allowfullscreen: "true",
          loading: "lazy",
          referrerpolicy: "strict-origin-when-cross-origin",
          frameborder: "0",
        },
      ],
    ];
  },

  addCommands() {
    return {
      setVideoEmbed:
        (options) =>
        ({ commands }) =>
          isEmbedUrl(options.src) ? commands.insertContent({ type: this.name, attrs: { src: options.src } }) : false,
    };
  },
});
