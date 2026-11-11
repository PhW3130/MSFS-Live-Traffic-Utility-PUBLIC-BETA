#include "../proxy/adsb_sources.h"

#include <iostream>
#include <string>

int main() {
    int failures = 0;
    const auto check = [&failures](bool condition, const char* message) {
        if (condition) std::cout << "[OK] " << message << "\n";
        else { std::cerr << "[FAIL] " << message << "\n"; ++failures; }
    };

    check(ltu::adsb::regional_url("adsb.fi", 60.3179, 24.9496, 250)
              == "https://opendata.adsb.fi/api/v3/lat/60.31790/lon/24.94960/dist/135",
          "adsb.fi regional requests use the official v3 route and NM");
    check(ltu::adsb::regional_url("adsb.lol", 50.0, 8.0, 250)
              == "https://api.adsb.lol/v2/lat/50.00000/lon/8.00000/dist/135",
          "adsb.lol keeps its v2 regional route");
    check(ltu::adsb::regional_url("adsb.fi", 0.0, 0.0, 1000).find("/dist/250")
              != std::string::npos,
          "regional distances are clamped to the documented 250 NM maximum");
    check(ltu::adsb::clamp_radius_km(1000) == 463,
          "collector metadata radius is clamped to the same 250 NM coverage");
    check(ltu::adsb::regional_url("adsb.fi", 0.0, 0.0, 1).find("/dist/1")
              != std::string::npos,
          "regional distances are clamped to at least 1 NM");
    return failures == 0 ? 0 : 1;
}
