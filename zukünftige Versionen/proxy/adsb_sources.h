#ifndef LTU_ADSB_SOURCES_H
#define LTU_ADSB_SOURCES_H

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace ltu {
namespace adsb {

static const int MAX_PUBLIC_RADIUS_NM = 250;
static const int MAX_PUBLIC_RADIUS_KM = 463; // 250 NM, rounded down conservatively

inline int clamp_radius_km(int radius_km) {
    return std::max(1, std::min(MAX_PUBLIC_RADIUS_KM, radius_km));
}

inline int radius_km_to_nm(int radius_km) {
    const int rounded = static_cast<int>(std::lround(
        static_cast<double>(clamp_radius_km(radius_km)) * 0.539957));
    return std::max(1, std::min(MAX_PUBLIC_RADIUS_NM, rounded));
}

inline const char* regional_base_url(const std::string& provider) {
    if (provider == "adsb.lol") return "https://api.adsb.lol/v2";
    if (provider == "adsb.fi") return "https://opendata.adsb.fi/api/v3";
    return nullptr;
}

inline std::string regional_url(const std::string& provider, double lat,
                                double lon, int radius_km) {
    const char* base = regional_base_url(provider);
    if (!base) return std::string();
    std::ostringstream url;
    url << base << "/lat/" << std::fixed << std::setprecision(5) << lat
        << "/lon/" << std::fixed << std::setprecision(5) << lon
        << "/dist/" << radius_km_to_nm(radius_km);
    return url.str();
}

// Keep the collector's ordinary regional failover order explicit and shared
// with its tests: OpenSky remains the third source; PlaneTrack is fourth.
inline const std::vector<std::string>& regional_fallback_order() {
    static const std::vector<std::string> order = {
        "adsb.lol", "adsb.fi", "opensky", "planetrack"
    };
    return order;
}

} // namespace adsb
} // namespace ltu

#endif
