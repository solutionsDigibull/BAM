// Editable text results use TipTap, the same editor as BAM_UI. Edits stay in the browser.
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

export function TextEditor({ content }: { content: string }) {
  const editor = useEditor({ extensions: [StarterKit], content });
  const button = (label: string, run: () => void, on?: boolean) => (
    <button key={label} type="button" title={label} className={"tt-b" + (on ? " on" : "")} onMouseDown={e => e.preventDefault()} onClick={run}>
      {label}
    </button>
  );
  return (
    <div className="tt-wrap">
      <div className="tt-bar">
        {button("B", () => editor?.chain().focus().toggleBold().run(), editor?.isActive("bold"))}
        {button("I", () => editor?.chain().focus().toggleItalic().run(), editor?.isActive("italic"))}
        {button("List", () => editor?.chain().focus().toggleBulletList().run(), editor?.isActive("bulletList"))}
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
