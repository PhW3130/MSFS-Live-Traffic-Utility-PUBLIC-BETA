#include "../proxy/client_demand.h"

#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

int main() {
    const std::string payload =
        "{\"ok\":true,\"active_regions\":2,\"regions\":["
        "{\"id\":\"D0123456789AB\",\"t\":1000,\"lat\":50.125,\"lon\":8.125,\"radius_km\":300},"
        "{\"id\":\"DABCDEF012345\",\"t\":999,\"lat\":-33.9,\"lon\":151.2,\"radius_km\":463}]}";
    std::vector<ltu::demand::Region> regions;
    assert(ltu::demand::parse_regions_json(payload, regions));
    assert(regions.size() == 2);
    assert(regions[0].id == "D0123456789AB");
    assert(regions[0].lat == 50.125 && regions[0].lon == 8.125);
    assert(regions[0].radius_km == 300 && regions[0].t == 1000);
    assert(ltu::demand::is_fresh(regions[0], 1020));
    assert(!ltu::demand::is_fresh(regions[0], 1021));

    assert(ltu::demand::parse_global_search_requested(
        "{\"ok\":true,\"global_search\":true,\"regions\":[]}"));
    assert(!ltu::demand::parse_global_search_requested(
        "{\"ok\":true,\"global_search\":false,\"regions\":[]}"));
    assert(!ltu::demand::parse_global_search_requested(
        "{\"ok\":true,\"regions\":[]}"));

    std::vector<ltu::demand::Region> empty;
    assert(ltu::demand::parse_regions_json("{\"ok\":true,\"regions\":[]}", empty));
    assert(empty.empty());
    assert(!ltu::demand::parse_regions_json("{\"error\":\"unauthorized\"}", empty));
    assert(!ltu::demand::parse_regions_json(
        "{\"regions\":[{\"id\":\"D0123456789AB\",\"t\":1000,\"lat\":91,\"lon\":8,\"radius_km\":10}]}", empty));
    assert(!ltu::demand::parse_regions_json(
        "{\"regions\":[{\"id\":\"../bad\",\"t\":1000,\"lat\":50,\"lon\":8,\"radius_km\":10}]}", empty));
    assert(!ltu::demand::parse_regions_json(
        "{\"regions\":[{\"id\":\"D0123456789AB\",\"t\":1000,\"lat\":50,\"lon\":8,\"radius_km\":464}]}", empty));
    assert(!ltu::demand::parse_regions_json(payload, empty, 1));

    ltu::demand::Region europe;
    europe.id = "D111111111111";
    europe.lat = 50.125;
    europe.lon = 8.125;
    europe.radius_km = 100;
    europe.t = 1000;
    ltu::demand::Region far_away;
    far_away.id = "D222222222222";
    far_away.lat = -33.9;
    far_away.lon = 151.2;
    far_away.radius_km = 50;
    far_away.t = 1000;
    assert(ltu::demand::overlaps(regions[0], europe));
    assert(!ltu::demand::overlaps(regions[0], far_away));

    std::printf("Client-demand parser and expiry checks passed\n");
    return 0;
}
