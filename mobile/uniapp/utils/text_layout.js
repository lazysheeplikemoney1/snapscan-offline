// Geometric rows shared with Java ReadingOrder and Python text_layout.
// References original indices so corrections and drafts never change identity.
export function buildTextLayout(boxes) {
  const source = Array.isArray(boxes) ? boxes : [];
  const finish = (rows, mode) => ({ rows, mode,
    text: rows.map(row => row.map(i => source[i] && typeof source[i].text === "string" ? source[i].text : "")
      .filter(text => text !== "").join("  ")).filter(text => text !== "").join("\n") });
  const fallback = () => finish(source.map((_, index) => [index]), "original_order");
  if (!source.length) return fallback();
  const angles = [];
  for (const item of source) {
    const q = item && item.box;
    if (!Array.isArray(q) || q.length !== 4 || q.some(p => !Array.isArray(p) || p.length !== 2 ||
      p.some(v => typeof v !== "number" || !Number.isFinite(v)))) return fallback();
    for (let i = 0; i < 4; i++) {
      const a = q[i], b = q[(i + 1) % 4], c = q[(i + 2) % 4];
      const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
      if (!Number.isFinite(cross) || cross <= 1e-6 || Math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-6) return fallback();
    }
    const angle = Math.atan2(q[1][1] - q[0][1] + q[2][1] - q[3][1],
      q[1][0] - q[0][0] + q[2][0] - q[3][0]);
    if (Math.abs(angle) > Math.PI / 4) return fallback();
    angles.push(angle);
  }
  angles.sort((a, b) => a - b);
  const middle = Math.floor(angles.length / 2);
  const angle = angles.length % 2 ? angles[middle] : (angles[middle - 1] + angles[middle]) / 2;
  const cosine = Math.cos(angle), sine = Math.sin(angle);
  const items = source.map((item, index) => {
    const us = item.box.map(p => p[0] * cosine + p[1] * sine);
    const vs = item.box.map(p => -p[0] * sine + p[1] * cosine);
    const top = Math.min(...vs), bottom = Math.max(...vs);
    return { index, left: Math.min(...us), right: Math.max(...us), top, bottom, height: bottom - top, center: (top + bottom) / 2 };
  });
  if (items.some(item => !Number.isFinite(item.height) || item.height <= 1e-6)) return fallback();
  // Let body text anchor a row before tiny marginal fragments. The anchor then
  // stays fixed so a chain of slightly lower fragments cannot swallow two rows.
  items.sort((a, b) => b.height - a.height || a.center - b.center || a.left - b.left || a.index - b.index);
  const rows = [];
  for (const item of items) {
    let selected = null, distance = Infinity;
    for (const row of rows) {
      const seed = row.seed, height = Math.min(item.height, seed.height);
      const offset = Math.abs(item.center - seed.center) / height;
      const overlap = (Math.min(item.bottom, seed.bottom) - Math.max(item.top, seed.top)) / height;
      if (offset <= 0.6 && overlap >= 0.3 && offset < distance) {
        // Tall envelopes around curved paragraph lines can overlap in height.
        // Check every member, including fragments added after the fixed seed.
        const crossLine = row.items.some(other =>
          Math.max(0, Math.min(item.right, other.right) - Math.max(item.left, other.left)) >
            0.5 * Math.min(item.right - item.left, other.right - other.left) &&
          Math.abs(item.center - other.center) > 0.2 * Math.min(item.height, other.height));
        if (!crossLine) { selected = row; distance = offset; }
      }
    }
    if (selected) selected.items.push(item);
    else rows.push({ seed: item, items: [item] });
  }
  rows.sort((a, b) => a.seed.center - b.seed.center || a.seed.left - b.seed.left || a.seed.index - b.seed.index);
  return finish(rows.map(row => row.items.sort((a, b) => a.left - b.left || a.index - b.index).map(item => item.index)), "spatial");
}
