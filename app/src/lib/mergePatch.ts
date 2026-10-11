// RFC 7386 merge patch, CHROMA flavour: the State never removes keys, so `null`
// means "the value is now null" (see hub/ipc.py). Objects merge; arrays and
// scalars are replaced.
export function applyMergePatch<T>(target: T, patch: unknown): T {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch as T;
  const base: Record<string, unknown> =
    target && typeof target === "object" && !Array.isArray(target) ? { ...(target as Record<string, unknown>) } : {};
  for (const [k, v] of Object.entries(patch as Record<string, unknown>)) {
    const cur = base[k];
    base[k] =
      v && typeof v === "object" && !Array.isArray(v) && cur && typeof cur === "object" && !Array.isArray(cur)
        ? applyMergePatch(cur, v)
        : v;
  }
  return base as T;
}
