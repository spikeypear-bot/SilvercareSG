/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Cognito user pool ID, e.g. ap-southeast-1_XXXXXXXXX. Not a secret. */
  readonly VITE_USER_POOL_ID?: string
  /** Cognito app client ID for silvercare-web. Not a secret. */
  readonly VITE_USER_POOL_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
