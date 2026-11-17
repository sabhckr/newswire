"use client";

import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Heading2,
  Heading3,
  Italic,
  Link2,
  Link2Off,
  List,
  ListOrdered,
  Minus,
  Quote,
  Redo2,
  Undo2,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tip } from "@/components/ui/tooltip";
import { cn, formatNumber } from "@/lib/utils";

function countWords(text: string): number {
  const m = text.trim().match(/[\p{L}\p{N}_]+/gu);
  return m ? m.length : 0;
}

function ToolButton({
  label,
  shortcut,
  active,
  disabled,
  onClick,
  children,
}: {
  label: string;
  shortcut?: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tip content={shortcut ? `${label} (${shortcut})` : label}>
      <button
        type="button"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={active}
        className={cn(
          "flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40",
          active && "bg-accent/10 text-accent hover:bg-accent/15 hover:text-accent",
        )}
      >
        {children}
      </button>
    </Tip>
  );
}

function LinkControl({ editor, disabled }: { editor: Editor; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState("");
  const active = editor.isActive("link");

  useEffect(() => {
    if (open) setHref((editor.getAttributes("link").href as string | undefined) ?? "https://");
  }, [open, editor]);

  const apply = () => {
    const url = href.trim();
    if (!url || url === "https://") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
    } else {
      const safe = /^(https?:|mailto:)/i.test(url) ? url : `https://${url}`;
      editor.chain().focus().extendMarkRange("link").setLink({ href: safe }).run();
    }
    setOpen(false);
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label={active ? "Edit link" : "Add link"}
            aria-pressed={active}
            onMouseDown={(e) => e.preventDefault()}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40",
              active && "bg-accent/10 text-accent",
            )}
          >
            <Link2 className="h-4 w-4" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              apply();
            }}
            className="flex flex-col gap-2"
          >
            <label htmlFor="nw-link-url" className="text-xs font-medium">
              Link address
            </label>
            <Input id="nw-link-url" value={href} onChange={(e) => setHref(e.target.value)} autoFocus placeholder="https://" />
            <p className="text-2xs text-muted">Links open with rel=&quot;nofollow noopener&quot;. Only http(s) and mailto links are kept.</p>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" type="submit">
                Apply
              </Button>
            </div>
          </form>
        </PopoverContent>
      </Popover>
      <ToolButton label="Remove link" disabled={disabled || !active} onClick={() => editor.chain().focus().unsetLink().run()}>
        <Link2Off className="h-4 w-4" />
      </ToolButton>
    </>
  );
}

/**
 * Article body editor. The parent reads HTML through `onReady(editor)` when saving, so typing does
 * not re-render the whole page; `onEdit` fires on every user change (used for the dirty flag).
 */
export function RichEditor({
  initialHtml,
  onReady,
  onEdit,
  readOnly,
}: {
  initialHtml: string;
  onReady: (editor: Editor | null) => void;
  onEdit: () => void;
  readOnly?: boolean;
}) {
  const [words, setWords] = useState(0);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [2, 3] } }),
      Link.configure({
        openOnClick: false,
        autolink: true,
        protocols: ["http", "https", "mailto"],
        HTMLAttributes: { rel: "noopener noreferrer nofollow", target: null },
      }),
      Placeholder.configure({ placeholder: "Write the story in your own words…" }),
    ],
    content: initialHtml,
    editable: !readOnly,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: "prose-article min-h-[420px] px-4 py-3 outline-none sm:px-6",
        "aria-label": "Article body",
        role: "textbox",
        "aria-multiline": "true",
      },
    },
    onCreate: ({ editor }) => setWords(countWords(editor.getText())),
    onUpdate: ({ editor }) => {
      setWords(countWords(editor.getText()));
      onEdit();
    },
  });

  useEffect(() => {
    onReady(editor);
    return () => onReady(null);
  }, [editor, onReady]);

  useEffect(() => {
    // emitUpdate=false: toggling editability is not a content change (it would mark the draft dirty).
    editor?.setEditable(!readOnly, false);
  }, [editor, readOnly]);

  const disabled = !editor || readOnly;
  return (
    <div className="flex flex-col">
      <div
        role="toolbar"
        aria-label="Formatting"
        className="sticky top-12 z-10 flex flex-wrap items-center gap-0.5 border-y border-border bg-surface/95 px-2 py-1 backdrop-blur"
      >
        {editor ? (
          <>
            <ToolButton label="Bold" shortcut="⌘B" active={editor.isActive("bold")} disabled={disabled} onClick={() => editor.chain().focus().toggleBold().run()}>
              <Bold className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Italic" shortcut="⌘I" active={editor.isActive("italic")} disabled={disabled} onClick={() => editor.chain().focus().toggleItalic().run()}>
              <Italic className="h-4 w-4" />
            </ToolButton>
            <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            <ToolButton label="Section heading" active={editor.isActive("heading", { level: 2 })} disabled={disabled} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
              <Heading2 className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Subheading" active={editor.isActive("heading", { level: 3 })} disabled={disabled} onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
              <Heading3 className="h-4 w-4" />
            </ToolButton>
            <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            <ToolButton label="Bulleted list" active={editor.isActive("bulletList")} disabled={disabled} onClick={() => editor.chain().focus().toggleBulletList().run()}>
              <List className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Numbered list" active={editor.isActive("orderedList")} disabled={disabled} onClick={() => editor.chain().focus().toggleOrderedList().run()}>
              <ListOrdered className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Quote" active={editor.isActive("blockquote")} disabled={disabled} onClick={() => editor.chain().focus().toggleBlockquote().run()}>
              <Quote className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Divider" disabled={disabled} onClick={() => editor.chain().focus().setHorizontalRule().run()}>
              <Minus className="h-4 w-4" />
            </ToolButton>
            <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            <LinkControl editor={editor} disabled={disabled} />
            <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            <ToolButton label="Undo" shortcut="⌘Z" disabled={disabled || !editor.can().undo()} onClick={() => editor.chain().focus().undo().run()}>
              <Undo2 className="h-4 w-4" />
            </ToolButton>
            <ToolButton label="Redo" shortcut="⇧⌘Z" disabled={disabled || !editor.can().redo()} onClick={() => editor.chain().focus().redo().run()}>
              <Redo2 className="h-4 w-4" />
            </ToolButton>
          </>
        ) : (
          <span className="h-7 text-xs leading-7 text-muted">Loading editor…</span>
        )}
        <span className="tabular ml-auto pr-1 text-2xs text-muted" aria-live="polite">
          {formatNumber(words)} words
        </span>
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
