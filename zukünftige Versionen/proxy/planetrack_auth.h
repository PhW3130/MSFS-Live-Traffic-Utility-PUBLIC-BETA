#ifndef LTU_PLANETRACK_AUTH_H
#define LTU_PLANETRACK_AUTH_H

#include "adsb_sources.h"
#include "opensky_auth.h"

#include <iomanip>
#include <sstream>
#include <cctype>
#include <fstream>
#include <iterator>
#include <string>

namespace ltu {
namespace planetrack {

static const unsigned int MIN_REQUEST_INTERVAL_MS = 2500;

inline bool read_api_key_json(const std::string& json, std::string& api_key) {
    api_key.clear();
    if (json.size() > 65536) return false;
    std::string value;
    if (!opensky::json_string_field(json, "planeTrackApiKey", value)) return false;

    const std::string::size_type first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return false;
    const std::string::size_type last = value.find_last_not_of(" \t\r\n");
    value = value.substr(first, last - first + 1);
    if (value.empty() || value.compare(0, 6, "PASTE_") == 0) return false;

    // Header values must not permit credential/header injection.
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it) {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c < 0x20 || c == 0x7f) return false;
    }
    api_key.swap(value);
    return true;
}

inline bool read_api_key_file(const std::string& path, std::string& api_key) {
    api_key.clear();
    std::ifstream file(path.c_str(), std::ios::in | std::ios::binary);
    if (!file.is_open()) return false;
    const std::string json((std::istreambuf_iterator<char>(file)),
                           std::istreambuf_iterator<char>());
    return read_api_key_json(json, api_key);
}

class Auth {
public:
    bool load_credentials(const std::string& path) {
        api_key_.clear();
        enabled_ = read_api_key_file(path, api_key_);
        if (!enabled_) api_key_.clear();
        return enabled_;
    }
    bool enabled() const { return enabled_; }
    const std::string& api_key() const { return api_key_; }
private:
    std::string api_key_;
    bool enabled_ = false;
};

inline std::string regional_url(double lat, double lon, int radius_km) {
    std::ostringstream url;
    url << "https://planetrack.ai/v1/sim/v2/point/"
        << std::fixed << std::setprecision(5) << lat << '/'
        << std::fixed << std::setprecision(5) << lon << '/'
        << adsb::radius_km_to_nm(radius_km);
    return url.str();
}

} // namespace planetrack
} // namespace ltu

#endif
