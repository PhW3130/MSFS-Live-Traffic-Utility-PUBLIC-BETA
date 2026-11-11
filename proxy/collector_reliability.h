#ifndef LTU_COLLECTOR_RELIABILITY_H
#define LTU_COLLECTOR_RELIABILITY_H

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <string>
#include <vector>

namespace ltu {
namespace reliability {

inline std::string trim_http_value(const std::string& value) {
    std::string::size_type first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return std::string();
    std::string::size_type last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

inline int month_number(const std::string& month) {
    static const char* months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
    for (int i = 0; i < 12; ++i) if (month == months[i]) return i + 1;
    return 0;
}

inline long long days_from_civil(int year, unsigned int month, unsigned int day) {
    year -= month <= 2;
    const int era = (year >= 0 ? year : year - 399) / 400;
    const unsigned int year_of_era = static_cast<unsigned int>(year - era * 400);
    const unsigned int adjusted_month = month > 2 ? month - 3 : month + 9;
    const unsigned int day_of_year = (153 * adjusted_month + 2) / 5 + day - 1;
    const unsigned int day_of_era = year_of_era * 365 + year_of_era / 4
                                  - year_of_era / 100 + day_of_year;
    return static_cast<long long>(era) * 146097 + static_cast<long long>(day_of_era) - 719468;
}

inline bool valid_date_parts(int year, int month, int day,
                             int hour, int minute, int second) {
    if (year < 1970 || month < 1 || month > 12 || day < 1
        || hour < 0 || hour > 23 || minute < 0 || minute > 59
        || second < 0 || second > 60) return false;
    static const int month_days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int maximum = month_days[month - 1];
    const bool leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    if (month == 2 && leap) ++maximum;
    return day <= maximum;
}

inline bool parse_http_date(const std::string& value, long long& epoch_seconds) {
    char weekday[16] = {0}, month_name[4] = {0}, zone[4] = {0};
    int day = 0, year = 0, hour = 0, minute = 0, second = 0;
    const std::string::size_type comma = value.find(',');
    if (comma != std::string::npos) {
        if (comma >= sizeof(weekday)) return false;
        const std::string weekday_text = value.substr(0, comma);
        if (std::sscanf(value.c_str(), "%15[^,], %d %3s %d %d:%d:%d %3s",
                        weekday, &day, month_name, &year,
                        &hour, &minute, &second, zone) == 8) {
            if (weekday_text.size() != 3 || std::string(zone) != "GMT") return false;
        } else {
            int short_year = 0;
            if (std::sscanf(value.c_str(), "%15[^,], %d-%3s-%2d %d:%d:%d %3s",
                            weekday, &day, month_name, &short_year,
                            &hour, &minute, &second, zone) != 8
                || weekday_text.size() < 6 || std::string(zone) != "GMT") return false;
            year = short_year <= 49 ? 2000 + short_year : 1900 + short_year;
        }
    } else {
        // Obsolete ANSI C asctime-date form (interpreted as GMT per HTTP-date).
        if (std::sscanf(value.c_str(), "%3s %3s %d %d:%d:%d %d",
                        weekday, month_name, &day, &hour, &minute, &second, &year) != 7
            || std::string(weekday).size() != 3) return false;
    }

    const int month = month_number(month_name);
    if (!valid_date_parts(year, month, day, hour, minute, second)) return false;
    epoch_seconds = days_from_civil(year, static_cast<unsigned int>(month),
                                    static_cast<unsigned int>(day)) * 86400LL
                  + hour * 3600LL + minute * 60LL + second;
    return true;
}

inline int retry_after_seconds(const std::string& header, std::time_t now,
                               int default_seconds) {
    const std::string value = trim_http_value(header);
    if (value.empty()) return default_seconds;

    char* end = nullptr;
    errno = 0;
    const long numeric = std::strtol(value.c_str(), &end, 10);
    if (end != value.c_str() && errno != ERANGE) {
        while (*end == ' ' || *end == '\t') ++end;
        if (*end == '\0' && numeric > 0)
            return static_cast<int>(std::min(86400L, numeric));
    }

    long long target = 0;
    if (!parse_http_date(value, target)) return default_seconds;
    const long long delay = target - static_cast<long long>(now);
    if (delay <= 0) return default_seconds;
    return static_cast<int>(std::min(86400LL, delay));
}

class OpenSkyQuotaGuard {
public:
    enum { MIN_INTERVAL_SECONDS = 120, DEFAULT_DAILY_REQUEST_LIMIT = 700 };
    static long long window_seconds() { return 24LL * 60LL * 60LL; }

    explicit OpenSkyQuotaGuard(const std::string& ledger_path,
                               int daily_request_limit = DEFAULT_DAILY_REQUEST_LIMIT)
        : ledger_path_(ledger_path),
          daily_request_limit_(std::max(1, std::min(
              static_cast<int>(DEFAULT_DAILY_REQUEST_LIMIT), daily_request_limit))),
          loaded_(false), storage_ok_(true) {}

    bool storage_ok() const { return loaded_ && storage_ok_; }

    // Records an attempted /states/all request before it is sent. This is
    // deliberately conservative: even an ambiguous network failure consumes budget.
    bool reserve(std::time_t now, int& wait_seconds) {
        wait_seconds = 0;
        if (!load()) return false; // Fail closed if the ledger cannot be read.
        prune(now);

        long long wait = 0;
        if (!requests_.empty()) {
            const std::time_t latest = *std::max_element(requests_.begin(), requests_.end());
            const long long earliest = static_cast<long long>(latest)
                                     + MIN_INTERVAL_SECONDS - static_cast<long long>(now);
            if (earliest > wait) wait = earliest;
        }
        if (static_cast<int>(requests_.size()) >= daily_request_limit_) {
            const long long daily = static_cast<long long>(requests_.front())
                                  + window_seconds() - static_cast<long long>(now);
            if (daily > wait) wait = daily;
        }
        if (wait > 0) {
            wait_seconds = static_cast<int>(std::min(86400LL, wait));
            return false;
        }

        const std::vector<std::time_t>::iterator inserted =
            std::upper_bound(requests_.begin(), requests_.end(), now);
        const std::vector<std::time_t>::iterator reserved = requests_.insert(inserted, now);
        if (!append_timestamp(now)) {
            requests_.erase(reserved);
            storage_ok_ = false;
            wait_seconds = 3600;
            return false;
        }
        return true;
    }

private:
    bool load() {
        if (loaded_) return storage_ok_;
        loaded_ = true;
        errno = 0;
        std::FILE* file = std::fopen(ledger_path_.c_str(), "rb");
        if (!file) {
            if (errno == ENOENT) return true;
            storage_ok_ = false;
            return false;
        }

        char line[128];
        while (std::fgets(line, sizeof(line), file)) {
            char* end = nullptr;
            errno = 0;
            const long long timestamp = std::strtoll(line, &end, 10);
            if (end == line || errno == ERANGE) {
                std::fclose(file);
                storage_ok_ = false;
                return false;
            }
            while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') ++end;
            if (*end != '\0' || timestamp <= 0) {
                std::fclose(file);
                storage_ok_ = false;
                return false;
            }
            requests_.push_back(static_cast<std::time_t>(timestamp));
        }
        if (std::ferror(file)) storage_ok_ = false;
        std::fclose(file);
        if (!storage_ok_) return false;
        std::sort(requests_.begin(), requests_.end());
        return true;
    }

    void prune(std::time_t now) {
        const long long cutoff = static_cast<long long>(now) - window_seconds();
        std::vector<std::time_t>::iterator first = requests_.begin();
        while (first != requests_.end()
               && static_cast<long long>(*first) <= cutoff) ++first;
        requests_.erase(requests_.begin(), first);
    }

    bool append_timestamp(std::time_t now) {
        std::ofstream out(ledger_path_.c_str(), std::ios::out | std::ios::app | std::ios::binary);
        if (!out) return false;
        out << static_cast<long long>(now) << "\r\n";
        out.flush();
        return out.good();
    }

    std::string ledger_path_;
    int daily_request_limit_;
    bool loaded_;
    bool storage_ok_;
    std::vector<std::time_t> requests_;
};

} // namespace reliability
} // namespace ltu

#endif
