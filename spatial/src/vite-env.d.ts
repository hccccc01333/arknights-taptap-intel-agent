/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEMO_MODE?: string;
  readonly VITE_PUBLIC_RESULTS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
