export interface ObserverOptions {
  enabled?: boolean;
  endpoint?: string;
  projectId?: string;
  token?: string;
  serviceName?: string;
  environment?: string;
  capacity?: number;
  exportTimeoutMs?: number;
  observationTimeoutMs?: number;
  fetch?: typeof fetch;
  exportFetch?: typeof fetch;
}
export interface Observer {
  fetch: typeof fetch;
  run<T>(
    metadata: { workflowId?: string; callSiteId?: string },
    fn: () => T,
  ): T;
  flush(options?: { timeoutMs?: number }): Promise<boolean>;
  close(options?: {
    timeoutMs?: number;
  }): Promise<Record<string, number | boolean>>;
  health(): Record<string, number | boolean>;
}
export function createObserver(options?: ObserverOptions): Observer;
