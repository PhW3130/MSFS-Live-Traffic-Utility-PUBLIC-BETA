#include "../proxy/adsb_sources.h"
#include "../proxy/planetrack_auth.h"

#include <iostream>
#include <string>
#include <vector>

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

    check(ltu::planetrack::regional_url(50.033, 8.57, 74)
              == "https://planetrack.ai/v1/sim/v2/point/50.03300/8.57000/40",
          "PlaneTrack uses its regional point endpoint and the demand radius in NM");
    check(ltu::planetrack::regional_url(0.0, 0.0, 1000).find("/250")
              != std::string::npos,
          "PlaneTrack request radius is capped at the public 250 NM maximum");
    const std::vector<std::string>& fallback = ltu::adsb::regional_fallback_order();
    check(fallback.size() == 4 && fallback[0] == "adsb.lol"
              && fallback[1] == "adsb.fi" && fallback[2] == "opensky"
              && fallback[3] == "planetrack",
          "PlaneTrack is the fourth regional source; OpenSky remains third");

    std::string api_key;
    check(ltu::planetrack::read_api_key_json(
              "{\"planeTrackApiKey\": \"  local-secret  \"}", api_key)
              && api_key == "local-secret",
          "PlaneTrack credentials load from the collector-only JSON field");
    check(!ltu::planetrack::read_api_key_json(
              "{\"planeTrackApiKey\": \"PASTE_KEY_HERE\"}", api_key)
              && api_key.empty(),
          "the tracked PlaneTrack template is rejected and failed reads clear the output key");
    check(!ltu::planetrack::read_api_key_json(
              "{\"planeTrackApiKey\": \"valid\\r\\nX-LTU-Token: injected\"}", api_key)
              && api_key.empty(),
          "PlaneTrack API keys containing header-control characters are rejected");
    return failures == 0 ? 0 : 1;
}
