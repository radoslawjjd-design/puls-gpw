const test = require('node:test');
const assert = require('node:assert');
const { computeTreemapLayout } = require('../static/js/treemap-layout.js');

test('zero items returns empty array', () => {
  const result = computeTreemapLayout([], 800, 600);
  assert.deepStrictEqual(result, []);
});

test('single item fills the whole container', () => {
  const items = [{ position_value_pln: 100 }];
  const result = computeTreemapLayout(items, 800, 600);
  assert.strictEqual(result.length, 1);
  const cell = result[0];
  assert.strictEqual(cell.x, 0);
  assert.strictEqual(cell.y, 0);
  assert.strictEqual(cell.width, 800);
  assert.strictEqual(cell.height, 600);
});

test('two equal-value items split the container ~50/50 by area', () => {
  const items = [
    { position_value_pln: 100 },
    { position_value_pln: 100 },
  ];
  const result = computeTreemapLayout(items, 800, 600);
  assert.strictEqual(result.length, 2);
  const totalArea = 800 * 600;
  for (const cell of result) {
    const area = cell.width * cell.height;
    assert.ok(Math.abs(area - totalArea / 2) < 1, `area ${area} should be ~${totalArea / 2}`);
  }
});

test('relative areas are proportional to input values within a small tolerance', () => {
  const items = [
    { position_value_pln: 300 },
    { position_value_pln: 100 },
  ];
  const result = computeTreemapLayout(items, 800, 600);
  assert.strictEqual(result.length, 2);
  const totalArea = 800 * 600;
  const totalValue = 400;
  for (let i = 0; i < items.length; i++) {
    const expectedArea = (items[i].position_value_pln / totalValue) * totalArea;
    const actualArea = result[i].width * result[i].height;
    assert.ok(
      Math.abs(actualArea - expectedArea) / expectedArea < 0.05,
      `item ${i}: area ${actualArea} should be ~${expectedArea}`
    );
  }
});

test('all items are placed with positive width and height', () => {
  const items = [
    { position_value_pln: 50 },
    { position_value_pln: 30 },
    { position_value_pln: 20 },
    { position_value_pln: 10 },
  ];
  const result = computeTreemapLayout(items, 800, 600);
  assert.strictEqual(result.length, 4);
  for (const cell of result) {
    assert.ok(cell.width > 0, 'width must be positive');
    assert.ok(cell.height > 0, 'height must be positive');
    assert.ok(cell.item, 'cell must reference its source item');
  }
});

test('items with non-positive position_value_pln are excluded from the layout', () => {
  const items = [
    { position_value_pln: 0 },
    { position_value_pln: 50 },
  ];
  const result = computeTreemapLayout(items, 800, 600);
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].item.position_value_pln, 50);
  assert.strictEqual(result[0].width, 800);
  assert.strictEqual(result[0].height, 600);
});

test('all-zero-value input returns an empty array, never NaN rects', () => {
  const items = [{ position_value_pln: 0 }, { position_value_pln: 0 }];
  const result = computeTreemapLayout(items, 800, 600);
  assert.deepStrictEqual(result, []);
});
