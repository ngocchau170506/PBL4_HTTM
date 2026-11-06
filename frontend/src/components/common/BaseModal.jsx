import { X } from "lucide-react";
import { useEffect } from "react";

export default function BaseModal({
  isOpen,
  onClose,
  title,
  subtitle,
  icon: Icon,
  maxWidth = "max-w-lg",
  children,
  footer,
}) {
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="bg-brand-deep/80 fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4 backdrop-blur-md"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={`card-glass border-brand-border animate-fade-in-up my-auto flex max-h-[88vh] w-full flex-col overflow-hidden rounded-3xl border shadow-2xl ${maxWidth}`}
      >
        {/* HEADER CỐ ĐỊNH Ở ĐỈNH */}
        <div className="border-brand-border-dark bg-brand-dark/95 flex shrink-0 items-center justify-between border-b px-6 py-4">
          <div className="flex items-center gap-3">
            {Icon && (
              <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border">
                <Icon className="h-5 w-5" />
              </div>
            )}
            <div>
              <h3 className="text-brand-text-primary text-sm font-bold sm:text-base">
                {title}
              </h3>
              {subtitle && (
                <p className="text-brand-text-muted mt-0.5 text-[11px]">
                  {subtitle}
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-brand-text-muted hover:bg-brand-surface hover:text-brand-text-primary cursor-pointer rounded-full p-1.5 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* BODY TỰ ĐỘNG CUỘN  */}
        <div className="min-h-0 flex-1 overflow-y-auto p-6 text-xs">
          {children}
        </div>

        {/* FOOTER LUÔN NỔI RÕ Ở ĐÁY */}
        {footer && (
          <div className="border-brand-border-dark bg-brand-dark/95 flex shrink-0 flex-wrap items-center justify-end gap-2.5 border-t px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
