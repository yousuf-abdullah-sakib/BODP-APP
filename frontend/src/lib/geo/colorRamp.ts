export type ColorRampName = "Viridis" | "Plasma" | "RdBu" | "YlOrRd" | "Blues";

const RAMPS: Record<ColorRampName, [number, number, number][]> = {
  Viridis: [
    [68, 1, 84],
    [59, 82, 139],
    [33, 145, 140],
    [94, 201, 98],
    [253, 231, 37],
  ],
  Plasma: [
    [13, 8, 135],
    [126, 3, 168],
    [204, 71, 120],
    [248, 149, 64],
    [240, 249, 33],
  ],
  RdBu: [
    [178, 24, 43],
    [239, 138, 98],
    [247, 247, 247],
    [103, 169, 207],
    [33, 102, 172],
  ],
  YlOrRd: [
    [255, 255, 178],
    [254, 204, 92],
    [253, 141, 60],
    [240, 59, 32],
    [189, 0, 38],
  ],
  Blues: [
    [247, 251, 255],
    [198, 219, 239],
    [107, 174, 214],
    [33, 113, 181],
    [8, 48, 107],
  ],
};

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

/** Returns "rgb(r,g,b)" for a normalized value t in [0,1] across the named color ramp. */
export function rampColor(t: number, ramp: ColorRampName = "Viridis"): string {
  const stops = RAMPS[ramp];
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const localT = scaled - i;
  const [r1, g1, b1] = stops[i];
  const [r2, g2, b2] = stops[i + 1];
  const r = Math.round(lerp(r1, r2, localT));
  const g = Math.round(lerp(g1, g2, localT));
  const b = Math.round(lerp(b1, b2, localT));
  return `rgb(${r},${g},${b})`;
}

export function colorForValue(value: number, min: number, max: number, ramp: ColorRampName = "Viridis"): string {
  const t = max === min ? 0.5 : (value - min) / (max - min);
  return rampColor(t, ramp);
}

export type InterpolationDisplayMode = "continuous" | "classified" | "custom";

/** Returns the equal-interval class breakpoints for a value range, split into classCount bins. */
export function equalIntervalBreaks(min: number, max: number, classCount: number): number[] {
  const breaks: number[] = [];
  for (let i = 1; i < classCount; i++) breaks.push(min + ((max - min) * i) / classCount);
  return breaks;
}

/** Snaps a value to its equal-interval class and returns that class's ramp color (stepped, not smooth). */
export function classifyValue(value: number, min: number, max: number, classCount: number, ramp: ColorRampName = "Viridis"): string {
  if (max === min) return rampColor(0.5, ramp);
  const clamped = Math.min(max, Math.max(min, value));
  const classIndex = Math.min(classCount - 1, Math.floor(((clamped - min) / (max - min)) * classCount));
  const t = classCount === 1 ? 0.5 : classIndex / (classCount - 1);
  return rampColor(t, ramp);
}

/** Colors a value by which interval of a user-supplied, ascending breakpoint list it falls into. */
export function classifyByBreakpoints(value: number, breakpoints: number[], ramp: ColorRampName = "Viridis"): string {
  const sorted = [...breakpoints].sort((a, b) => a - b);
  let bucket = 0;
  while (bucket < sorted.length && value >= sorted[bucket]) bucket++;
  const bucketCount = sorted.length + 1;
  const t = bucketCount === 1 ? 0.5 : bucket / (bucketCount - 1);
  return rampColor(t, ramp);
}
