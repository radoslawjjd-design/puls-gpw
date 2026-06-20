function worstAspectRatio(row, total, width, height) {
  const rowTotal = row.reduce((sum, it) => sum + it.position_value_pln, 0);
  const rowFraction = rowTotal / total;
  let rects;
  if (width >= height) {
    const rowWidth = width * rowFraction;
    rects = row.map((item) => ({
      width: rowWidth,
      height: height * (item.position_value_pln / rowTotal),
    }));
  } else {
    const rowHeight = height * rowFraction;
    rects = row.map((item) => ({
      width: width * (item.position_value_pln / rowTotal),
      height: rowHeight,
    }));
  }
  return Math.max(...rects.map((r) => Math.max(r.width / r.height, r.height / r.width)));
}

function squarify(items, x, y, width, height) {
  if (items.length === 0) return [];
  if (items.length === 1) {
    return [{ item: items[0], x, y, width, height }];
  }

  const total = items.reduce((sum, it) => sum + it.position_value_pln, 0);

  let i = 1;
  while (i < items.length) {
    const row = items.slice(0, i);
    const rowPlus = items.slice(0, i + 1);
    if (worstAspectRatio(row, total, width, height) < worstAspectRatio(rowPlus, total, width, height)) {
      break;
    }
    i++;
  }

  const row = items.slice(0, i);
  const rest = items.slice(i);
  const rowTotal = row.reduce((sum, it) => sum + it.position_value_pln, 0);
  const rowFraction = rowTotal / total;

  let rowRects;
  let remaining;
  if (width >= height) {
    const rowWidth = width * rowFraction;
    let curY = y;
    rowRects = row.map((item) => {
      const itemHeight = height * (item.position_value_pln / rowTotal);
      const rect = { item, x, y: curY, width: rowWidth, height: itemHeight };
      curY += itemHeight;
      return rect;
    });
    remaining = { x: x + rowWidth, y, width: width - rowWidth, height };
  } else {
    const rowHeight = height * rowFraction;
    let curX = x;
    rowRects = row.map((item) => {
      const itemWidth = width * (item.position_value_pln / rowTotal);
      const rect = { item, x: curX, y, width: itemWidth, height: rowHeight };
      curX += itemWidth;
      return rect;
    });
    remaining = { x, y: y + rowHeight, width, height: height - rowHeight };
  }

  return rowRects.concat(squarify(rest, remaining.x, remaining.y, remaining.width, remaining.height));
}

function computeTreemapLayout(items, containerWidth, containerHeight) {
  if (!items || items.length === 0) return [];
  const positiveItems = items.filter((item) => item.position_value_pln > 0);
  if (positiveItems.length === 0) return [];
  return squarify(positiveItems, 0, 0, containerWidth, containerHeight);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { computeTreemapLayout };
}
