// A random key that marks one submit, so sending it twice saves it once.
// crypto.randomUUID only exists on https and localhost; lab machines may reach a dev server
// over plain http on the local network, where getRandomValues still works.
export function newKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
