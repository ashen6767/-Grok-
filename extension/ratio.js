globalThis.grokClosestVideoRatio = function grokClosestVideoRatio(width, height) {
  width = Number(width || 0);
  height = Number(height || 0);
  if (!width || !height) throw new Error("无法读取底图宽高");
  const actual = width / height;
  const candidates = [
    ["2:3", 2 / 3], ["3:2", 3 / 2], ["1:1", 1],
    ["9:16", 9 / 16], ["16:9", 16 / 9]
  ];
  return candidates.reduce((best, item) =>
    Math.abs(Math.log(actual / item[1])) < Math.abs(Math.log(actual / best[1])) ? item : best
  )[0];
};
