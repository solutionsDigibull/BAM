// Extended cost of a BOM line (synthetic sample)
function extendedCost(qty, unitPrice, scrapRate = 0.015) {
  return Math.round(qty * unitPrice * (1 + scrapRate) * 1e4) / 1e4;
}

module.exports = { extendedCost };
