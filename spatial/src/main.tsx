import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import V3App from './V3App';
const V1 = lazy(() => import('./FieldStudio'));
const V2 = lazy(() => import('./V2App'));
const version = new URLSearchParams(location.search).get('version');

createRoot(document.getElementById('root')!).render(<StrictMode>{version==='1'
  ? <Suspense fallback={<p>正在打开 V1…</p>}><V1 /></Suspense> : version==='2'
  ? <Suspense fallback={<p>正在打开 V2…</p>}><V2 /></Suspense> : <V3App />}</StrictMode>);
