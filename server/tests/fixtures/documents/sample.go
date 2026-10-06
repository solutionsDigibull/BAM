package bom

// ExtendedCost returns the cost of a line including scrap.
func ExtendedCost(qty int, unitPrice, scrapRate float64) float64 {
	return float64(qty) * unitPrice * (1 + scrapRate)
}
