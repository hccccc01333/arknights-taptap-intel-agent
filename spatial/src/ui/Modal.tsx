import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './Icon';

export function Modal({ title, eyebrow, onClose, children, className = '' }: { title: string; eyebrow?: string; onClose: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const scroll = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const content = Array.from(document.querySelectorAll('[data-app-content]')).map(node => ({ node, inert: node.hasAttribute('inert') }));
    content.forEach(({ node }) => node.setAttribute('inert', ''));
    const focusable = () => Array.from(ref.current?.querySelectorAll<HTMLElement>('button, input, textarea, select, a[href], [tabindex]') || [])
      .filter(node => node.tabIndex >= 0 && !node.matches(':disabled, [hidden], input[type="hidden"]') && !node.closest('[inert]') && node.getClientRects().length > 0);
    const nodes = focusable();
    (nodes.find(node => node.matches('input, textarea, [data-autofocus]')) || nodes[0] || ref.current)?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close.current(); }
      if (event.key !== 'Tab') return;
      const nodes = focusable();
      const first = nodes[0]; const last = nodes[nodes.length - 1];
      if (!first) { event.preventDefault(); ref.current?.focus(); }
      else if (!nodes.includes(document.activeElement as HTMLElement)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', key, true);
    return () => {
      document.body.style.overflow = scroll;
      content.forEach(({ node, inert }) => { if (!inert) node.removeAttribute('inert'); });
      document.removeEventListener('keydown', key, true);
      if (previous?.isConnected && !previous.closest('[inert]')) previous.focus();
    };
  }, []);
  return createPortal(<div className="modal-scrim centered v2-modal-scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={ref} tabIndex={-1} className={`v2-modal ${className}`} role="dialog" aria-modal="true" aria-label={title}>
      <header className="v2-modal-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label={`关闭${title}`}><Icon name="close" /></button></header>
      {children}
    </section>
  </div>, document.querySelector('[data-modal-root]') || document.body);
}
