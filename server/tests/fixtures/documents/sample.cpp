// Extended cost of a BOM line (synthetic sample)
#include <cstdint>

double extended_cost(std::int32_t qty, double unit_price, double scrap_rate) {
    return qty * unit_price * (1.0 + scrap_rate);
}
