import type { CSSProperties } from 'react';

const paths: Record<string, string> = {
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  orbit: 'M12 3a9 9 0 1 0 9 9 M4 18C0 14 15 0 20 4s-9 19-14 16 M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M20 2v4 M18 4h4',
  chart: 'M4 3v17h17 M8 14l4-5 4 3 5-7',
  file: 'M14 2H5v20h14V7z M14 2v5h5 M8 12h8 M8 16h6',
  layers: 'M12 3L2 8l10 5 10-5z M2 12l10 5 10-5 M2 16l10 5 10-5',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
  search: 'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5',
  arrow: 'M5 12h14 M14 7l5 5-5 5',
  chevron: 'M9 5l7 7-7 7',
  down: 'M6 9l6 6 6-6',
  plus: 'M12 5v14 M5 12h14',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M3 12h18 M12 3c-5 5-5 13 0 18 5-5 5-13 0-18',
  spark: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4',
  bell: 'M5 17h14l-2-3V9a5 5 0 0 0-10 0v5z M10 21h4',
  calendar: 'M4 5h16v16H4z M8 2v6 M16 2v6 M4 10h16',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  refresh: 'M20 7a9 9 0 0 0-15-2L2 8 M2 3v5h5 M4 17a9 9 0 0 0 15 2l3-3 M22 21v-5h-5',
  expand: 'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5',
  pause: 'M8 5v14 M16 5v14',
  play: 'M7 4l14 8-14 8z',
  check: 'M5 12l4 4L19 6',
  close: 'M5 5l14 14 M19 5L5 19',
  bookmark: 'M6 3h12v18l-6-4-6 4z',
  link: 'M10 13l4-4 M8 15l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M14 9l2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
  help: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4 M12 17h.01',
  pulse: 'M2 12h5l3-8 4 16 3-8h5',
  terminal: 'M4 5h16v14H4z M7 9l3 3-3 3 M13 15h4',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  shield: 'M12 2l9 4v6c0 6-9 10-9 10S3 18 3 12V6z M8 12l3 3 5-6',
  logout: 'M10 3H4v18h6 M10 12h11 M17 8l4 4-4 4',
};

export function Icon({ name, size = 18, style, className = '' }: { name: string; size?: number; style?: CSSProperties; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style} className={className}><path d={paths[name] || paths.spark} /></svg>;
}
