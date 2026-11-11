#ifndef LTU_CLIENT_DEMAND_H
#define LTU_CLIENT_DEMAND_H

#include "opensky_auth.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <set>
#include <string>
#include <vector>

namespace ltu {
namespace demand {

static const int DEFAULT_TTL_SECONDS = 20;
static const int MAX_RADIUS_KM = 463;
static const std::size_t MAX_REGIONS = 64;

struct Region {
    std::string id;
    double lat;
    double lon;
    int radius_km;
    std::time_t t;

    Region() : lat(0.0), lon(0.0), radius_km(0), t(0) {}
};

inline bool valid_region_id(const std::string& id) {
    if (id.size() != 13 || id[0] != 'D') return false;
    for (std::size_t i = 1; i < id.size(); ++i) {
        const char c = id[i];
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'))) return false;
    }
    return true;
}

inline bool valid_region(const Region& region) {
    return valid_region_id(region.id)
        && std::isfinite(region.lat) && region.lat >= -90.0 && region.lat <= 90.0
        && std::isfinite(region.lon) && region.lon >= -180.0 && region.lon <= 180.0
        && region.radius_km >= 1 && region.radius_km <= MAX_RADIUS_KM
        && region.t > 0;
}

inline bool is_fresh(const Region& region, std::time_t now,
                     int ttl_seconds = DEFAULT_TTL_SECONDS) {
    return valid_region(region) && region.t <= now
        && (now - region.t) <= ttl_seconds;
}

inline double radians(double degrees) {
    return degrees * 3.14159265358979323846 / 180.0;
}

inline double distance_km(double lat1, double lon1, double lat2, double lon2) {
    const double p1 = radians(lat1), p2 = radians(lat2);
    const double dp = radians(lat2 - lat1);
    double delta_lon = std::fmod(lon2 - lon1 + 540.0, 360.0);
    if (delta_lon < 0.0) delta_lon += 360.0;
    delta_lon -= 180.0;
    const double dl = radians(delta_lon);
    const double sin_p = std::sin(dp / 2.0);
    const double sin_l = std::sin(dl / 2.0);
    const double a = sin_p * sin_p + std::cos(p1) * std::cos(p2) * sin_l * sin_l;
    return 12742.0 * std::asin(std::sqrt(std::min(1.0, std::max(0.0, a))));
}

inline bool overlaps(const Region& a, const Region& b) {
    if (!valid_region(a) || !valid_region(b)) return false;
    return distance_km(a.lat, a.lon, b.lat, b.lon)
        <= static_cast<double>(a.radius_km + b.radius_km);
}

inline bool json_number_field(const std::string& json, const std::string& key,
                              double& value) {
    const std::string marker = "\"" + key + "\"";
    std::size_t pos = 0;
    while ((pos = json.find(marker, pos)) != std::string::npos) {
        std::size_t cursor = pos + marker.size();
        while (cursor < json.size() && std::isspace(static_cast<unsigned char>(json[cursor]))) ++cursor;
        if (cursor >= json.size() || json[cursor++] != ':') {
            pos += marker.size();
            continue;
        }
        while (cursor < json.size() && std::isspace(static_cast<unsigned char>(json[cursor]))) ++cursor;
        char* end = nullptr;
        const double parsed = std::strtod(json.c_str() + cursor, &end);
        if (end == json.c_str() + cursor || !std::isfinite(parsed)) return false;
        while (*end && std::isspace(static_cast<unsigned char>(*end))) ++end;
        if (*end != ',' && *end != '}') return false;
        value = parsed;
        return true;
    }
    return false;
}

inline bool json_region_object(const std::string& object, Region& region) {
    long long timestamp = 0;
    long long radius = 0;
    if (!ltu::opensky::json_string_field(object, "id", region.id)
        || !json_number_field(object, "lat", region.lat)
        || !json_number_field(object, "lon", region.lon)
        || !ltu::opensky::json_integer_field(object, "radius_km", radius)
        || !ltu::opensky::json_integer_field(object, "t", timestamp)) return false;
    if (radius < 0 || radius > MAX_RADIUS_KM || timestamp <= 0) return false;
    region.radius_km = static_cast<int>(radius);
    region.t = static_cast<std::time_t>(timestamp);
    return valid_region(region);
}

/** Parse the optional one-shot global-search demand flag from action=demand. */
inline bool parse_global_search_requested(const std::string& json) {
    const std::string marker = "\"global_search\"";
    const std::size_t found = json.find(marker);
    if (found == std::string::npos) return false;
    std::size_t pos = found + marker.size();
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
    if (pos >= json.size() || json[pos++] != ':') return false;
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
    if (json.compare(pos, 4, "true") != 0) return false;
    pos += 4;
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
    return pos < json.size() && (json[pos] == ',' || json[pos] == '}');
}

/** Parse the token-protected PHP action=demand response. */
inline bool parse_regions_json(const std::string& json, std::vector<Region>& regions,
                               std::size_t maximum = MAX_REGIONS) {
    regions.clear();
    const std::string marker = "\"regions\"";
    std::size_t pos = json.find(marker);
    if (pos == std::string::npos) return false;
    pos += marker.size();
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
    if (pos >= json.size() || json[pos++] != ':') return false;
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
    if (pos >= json.size() || json[pos++] != '[') return false;

    std::set<std::string> seen;
    while (pos < json.size()) {
        while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos]))) ++pos;
        if (pos >= json.size()) return false;
        if (json[pos] == ']') return true;
        if (json[pos] == ',') { ++pos; continue; }
        if (json[pos] != '{') return false;

        const std::size_t start = pos;
        int depth = 0;
        bool in_string = false;
        bool escaped = false;
        for (; pos < json.size(); ++pos) {
            const char c = json[pos];
            if (in_string) {
                if (escaped) escaped = false;
                else if (c == '\\') escaped = true;
                else if (c == '"') in_string = false;
                continue;
            }
            if (c == '"') in_string = true;
            else if (c == '{') ++depth;
            else if (c == '}') {
                --depth;
                if (depth == 0) { ++pos; break; }
                if (depth < 0) return false;
            }
        }
        if (depth != 0 || in_string) return false;
        const std::string object = json.substr(start, pos - start);
        Region region;
        if (!json_region_object(object, region) || !seen.insert(region.id).second
            || regions.size() >= maximum) return false;
        regions.push_back(region);
    }
    return false;
}

} // namespace demand
} // namespace ltu

#endif
