#include "../proxy/collector_reliability.h"

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {
int failures = 0;

void check(bool condition, const char* message) {
    if (condition) {
        std::cout << "[OK] " << message << "\n";
    } else {
        std::cerr << "[FAIL] " << message << "\n";
        ++failures;
    }
}
}

int main() {
    using ltu::reliability::retry_after_seconds;
    const std::time_t now = static_cast<std::time_t>(784111000); // 1994-11-06 08:36:40 GMT
    check(retry_after_seconds("12", now, 90) == 12,
          "Retry-After delta-seconds");
    check(retry_after_seconds("  12  ", now, 90) == 12,
          "Retry-After tolerates surrounding whitespace");
    check(retry_after_seconds("Sun, 06 Nov 1994 08:49:37 GMT", now, 90) == 777,
          "Retry-After IMF-fixdate");
    check(retry_after_seconds("Sunday, 06-Nov-94 08:49:37 GMT", now, 90) == 777,
          "Retry-After obsolete RFC 850 date");
    check(retry_after_seconds("Sun Nov  6 08:49:37 1994", now, 90) == 777,
          "Retry-After obsolete asctime date");
    check(retry_after_seconds("Sun, 06 Nov 1994 08:00:00 GMT", now, 90) == 90,
          "Expired Retry-After date falls back safely");
    check(retry_after_seconds("nonsense", now, 90) == 90,
          "Invalid Retry-After falls back safely");
    check(retry_after_seconds("999999", now, 90) == 86400,
          "Retry-After is capped at one day");

    const std::string ledger = "test_opensky_quota_ledger.tmp";
    std::remove(ledger.c_str());
    {
        ltu::reliability::OpenSkyQuotaGuard quota(ledger, 2);
        int wait = 0;
        check(quota.reserve(1000, wait) && wait == 0,
              "First persistent quota reservation succeeds");
        check(!quota.reserve(1119, wait) && wait == 1,
              "Minimum 120-second interval is enforced");
        check(quota.reserve(1120, wait),
              "Second reservation succeeds at 120 seconds");
        check(!quota.reserve(1240, wait) && wait == 86160,
              "Daily rolling cap is enforced");
    }
    {
        ltu::reliability::OpenSkyQuotaGuard restarted(ledger, 2);
        int wait = 0;
        check(!restarted.reserve(1200, wait) && wait == 86200,
              "Quota history survives collector restart");
        check(restarted.reserve(87400, wait),
              "Old request leaves the rolling 24-hour window");
    }
    std::remove(ledger.c_str());
    return failures == 0 ? 0 : 1;
}
