// ============================================================================
// ltu_collector.cpp – ADS-B-Sammler für MSFS Live Traffic Utility (Server-PC)
// ============================================================================
// Nachfragegesteuerte Sammlung:
//   * Der PHP-Proxy merkt sich nur grobe Bereiche aus aktuellen action=traffic-
//     Anfragen. Der Collector holt diese Bereiche token-geschuetzt ab.
//   * Ohne Nachfrage innerhalb von 20 s werden alle Upstream-Abfragen pausiert.
//   * Es werden ausschliesslich Regional-Feeds um aktive Client-Bereiche genutzt;
//     adsb.lol -> adsb.fi -> optional begrenztes OpenSky /states/all.
//   * Optional konfigurierte --region-Eintraege sind ein Allowlist-Filter und
//     starten selbst keine Abfragen. Es gibt keinen globalen Feed-/Rasterlauf.
//   * Provider werden feedweit auf mindestens 1 s Abstand gedrosselt; OpenSky
//     behaelt das persistente 120-s-/Tagesbudget ueber Collector-Neustarts hinweg.
//
// Der Proxy speichert pro grob gerastertem Nachfragebereich und liefert Clients
// nur den jeweils angefragten Kartenausschnitt aus. Die exakte Client-Position
// wird weder im Heartbeat noch in der oeffentlichen Status-API gespeichert.
//
// Build (Windows, keine externen Libraries):
//   MinGW:  g++ -O2 -static -pthread ltu_collector.cpp -o ltu_collector.exe -lwinhttp
//   MSVC:   cl /O2 /EHsc /MT ltu_collector.cpp /Fe:ltu_collector.exe /link winhttp.lib
//   oder:   build_collector.bat
//
// Aufruf:
//   ltu_collector.exe              <- EINFACH STARTEN, ohne Argumente!
//     Eingebaute Defaults: URL http://dnd.59-seconds.com/ltu/index.php,
//     Token 'CHANGE-ME-BITTE' (Repo-Default der PHP), Nachfrage-Poll 5 s.
//     Es werden nur aktuell angefragte Client-Bereiche gesammelt; ohne Nachfrage
//     fuer 20 s werden alle ADS-B-/OpenSky-Upstream-Abfragen pausiert.
//   Konfiguration optional per ltu_collector.ini NEBEN DER EXE (Vorlage:
//   ltu_collector.example.ini):
//     url=http://dnd.59-seconds.com/ltu/index.php
//     token=GEHEIMES_TOKEN         (muss zu LTU_TOKEN in der PHP passen)
//     interval=5
//     region=50.03,8.57,250        (wiederholbare Allowlist, keine eigenstaendige Abfrage)
//   CLI-Argumente haben Vorrang (Kompatibilitaet):
//   ltu_collector.exe --url ... --token GEHEIMES_TOKEN --interval 5
//                     --region 50.03,8.57,250 --region 52.52,13.40,250
// ============================================================================

#ifndef _WIN32
#error "ltu_collector ist ein Windows-Programm (WinHTTP)."
#endif

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

// Prevent Windows headers from defining min/max macros that break std::min/std::max.
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <winhttp.h>

#include "adsb_sources.h"
#include "client_demand.h"
#include "collector_reliability.h"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <fstream>
#include <string>
#include <thread>
#include <mutex>
#include <chrono>
#include <vector>
#include <map>
#include <ctime>
#include <iterator>

#include "opensky_auth.h"

#pragma comment(lib, "winhttp.lib")

namespace {

std::atomic<bool> g_running{true};

BOOL WINAPI ctrl_handler(DWORD type) {
    if (type == CTRL_C_EVENT || type == CTRL_BREAK_EVENT || type == CTRL_CLOSE_EVENT)
        g_running = false;
    return TRUE;
}

std::wstring to_wide(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

std::string from_wide(const wchar_t* data, int length) {
    if (!data || length <= 0) return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, data, length, nullptr, 0, nullptr, nullptr);
    if (n <= 0) return std::string();
    std::string result(static_cast<std::size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, data, length, &result[0], n, nullptr, nullptr);
    return result;
}

bool query_custom_header(HINTERNET request, const char* name, std::string& value) {
    value.clear();
    std::wstring wide_name = to_wide(name);
    DWORD bytes = 0;
    WinHttpQueryHeaders(request, WINHTTP_QUERY_CUSTOM, &wide_name[0], nullptr,
                        &bytes, WINHTTP_NO_HEADER_INDEX);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || bytes < sizeof(wchar_t)) return false;
    std::vector<wchar_t> buffer(bytes / sizeof(wchar_t) + 1, 0);
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_CUSTOM, &wide_name[0],
                             &buffer[0], &bytes, WINHTTP_NO_HEADER_INDEX)) return false;
    std::size_t length = bytes / sizeof(wchar_t);
    if (length && buffer[length - 1] == L'\0') --length;
    value = from_wide(&buffer[0], static_cast<int>(length));
    return !value.empty();
}

std::string now_str() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char buf[32];
    snprintf(buf, sizeof(buf), "%02d:%02d:%02d", st.wHour, st.wMinute, st.wSecond);
    return buf;
}

// ---------------------------------------------------------------------------
// Minimaler WinHTTP-Client (Session-Handle ist thread-sicher).
// ---------------------------------------------------------------------------
class HttpClient {
public:
    explicit HttpClient(const std::string& user_agent) {
        session_ = WinHttpOpen(to_wide(user_agent).c_str(),
                               WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                               WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (session_) WinHttpSetTimeouts(session_, 10000, 10000, 10000, 25000);
    }
    ~HttpClient() {
        if (session_) WinHttpCloseHandle(session_);
    }
    bool valid() const { return session_ != nullptr; }

    bool request(const std::string& url, const std::string& method,
                 const std::string& body, const std::string& extra_headers,
                 std::string& body_out, int& status_out,
                 std::map<std::string, std::string>* response_headers = nullptr,
                 DWORD* win32_error_out = nullptr) {
        body_out.clear();
        status_out = 0;
        if (response_headers) response_headers->clear();
        if (win32_error_out) *win32_error_out = 0;
        if (!session_) {
            if (win32_error_out) *win32_error_out = GetLastError();
            return false;
        }

        URL_COMPONENTS uc;
        ZeroMemory(&uc, sizeof(uc));
        uc.dwStructSize = sizeof(uc);
        wchar_t host[256] = {0}, path[4096] = {0}, extra[4096] = {0};
        uc.lpszHostName = host;   uc.dwHostNameLength = 256;
        uc.lpszUrlPath  = path;   uc.dwUrlPathLength  = 4096;
        uc.lpszExtraInfo = extra; uc.dwExtraInfoLength = 4096;
        uc.dwSchemeLength = 1;

        std::wstring wurl = to_wide(url);
        if (!WinHttpCrackUrl(wurl.c_str(), (DWORD)wurl.size(), 0, &uc)) {
            if (win32_error_out) *win32_error_out = GetLastError();
            return false;
        }
        const bool secure = (uc.nScheme == INTERNET_SCHEME_HTTPS);
        HINTERNET conn = WinHttpConnect(session_, host, uc.nPort, 0);
        if (!conn) {
            if (win32_error_out) *win32_error_out = GetLastError();
            return false;
        }

        std::wstring request_path(path);
        request_path += extra; // Query-Parameter sind fuer PHP-action=... entscheidend.
        HINTERNET req = WinHttpOpenRequest(conn, to_wide(method).c_str(), request_path.c_str(),
                                           nullptr, WINHTTP_NO_REFERER,
                                           WINHTTP_DEFAULT_ACCEPT_TYPES,
                                           secure ? WINHTTP_FLAG_SECURE : 0);
        if (!req) {
            const DWORD err = GetLastError();
            WinHttpCloseHandle(conn);
            if (win32_error_out) *win32_error_out = err;
            return false;
        }

        bool ok = true;
        if (!extra_headers.empty()) {
            std::wstring wh = to_wide(extra_headers);
            ok = WinHttpAddRequestHeaders(req, wh.c_str(), (DWORD)wh.size(),
                                          WINHTTP_ADDREQ_FLAG_ADD) != 0;
        }
        if (ok) {
            ok = WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                    body.empty() ? nullptr : (void*)body.data(),
                                    (DWORD)body.size(), (DWORD)body.size(), 0) != 0;
        }
        if (ok) ok = WinHttpReceiveResponse(req, nullptr) != 0;

        DWORD request_error = ok ? 0 : GetLastError();
        if (ok) {
            DWORD status = 0, sz = sizeof(status);
            WinHttpQueryHeaders(req,
                                WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                WINHTTP_HEADER_NAME_BY_INDEX, &status, &sz,
                                WINHTTP_NO_HEADER_INDEX);
            status_out = (int)status;

            if (response_headers) {
                const char* names[] = {
                    "Retry-After",
                    "X-Rate-Limit-Remaining",
                    "X-Rate-Limit-Retry-After-Seconds"
                };
                for (std::size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
                    std::string value;
                    if (query_custom_header(req, names[i], value))
                        (*response_headers)[names[i]] = value;
                }
            }

            DWORD avail = 0;
            while (WinHttpQueryDataAvailable(req, &avail) && avail > 0) {
                std::vector<char> buf(avail);
                DWORD readn = 0;
                if (!WinHttpReadData(req, buf.data(), avail, &readn)) {
                    request_error = GetLastError();
                    break;
                }
                body_out.append(buf.data(), readn);
            }
        }

        WinHttpCloseHandle(req);
        WinHttpCloseHandle(conn);
        if (win32_error_out) *win32_error_out = request_error;
        return ok && status_out >= 200 && status_out < 300;
    }

private:
    HINTERNET session_ = nullptr;
};

class ProviderRequestGate {
public:
    void wait(const std::string& provider, unsigned int min_interval_ms = 1000) {
        typedef std::chrono::steady_clock Clock;
        Clock::time_point scheduled;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            scheduled = Clock::now();
            std::map<std::string, Clock::time_point>::iterator it = next_allowed_.find(provider);
            if (it != next_allowed_.end() && it->second > scheduled) scheduled = it->second;
            next_allowed_[provider] = scheduled + std::chrono::milliseconds(min_interval_ms);
        }
        while (g_running && Clock::now() < scheduled) {
            const long long remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                scheduled - Clock::now()).count();
            if (remaining <= 0) break;
            Sleep(static_cast<DWORD>(std::min(100LL, remaining)));
        }
    }
private:
    std::mutex mutex_;
    std::map<std::string, std::chrono::steady_clock::time_point> next_allowed_;
};

ProviderRequestGate g_provider_gate;

class OpenSkyAuth {
public:
    bool load_credentials(const std::string& path) {
        std::ifstream file(path.c_str(), std::ios::in | std::ios::binary);
        if (!file.is_open()) return false;
        std::string json((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        if (json.size() > 65536) return false;
        loaded_ = ltu::opensky::read_credentials_json(json, client_id_, client_secret_);
        return loaded_;
    }

    bool enabled() const { return loaded_; }

    bool fetch_global(HttpClient& http, std::string& body, int& status,
                      std::map<std::string, std::string>& headers, DWORD& win32_error) {
        return fetch_states_url(http, "https://opensky-network.org/api/states/all",
                                body, status, headers, win32_error);
    }

    bool fetch_region(HttpClient& http, double lat, double lon, int radius_km,
                      std::string& body, int& status,
                      std::map<std::string, std::string>& headers, DWORD& win32_error) {
        const double lat_delta = std::max(0.01, radius_km / 111.0);
        const double cos_lat = std::max(0.01, std::fabs(std::cos(lat * 3.14159265358979323846 / 180.0)));
        const double lon_delta = std::min(180.0, radius_km / (111.0 * cos_lat));
        const double lat_min = std::max(-90.0, lat - lat_delta);
        const double lat_max = std::min(90.0, lat + lat_delta);
        double lon_min = lon - lon_delta;
        double lon_max = lon + lon_delta;
        // OpenSky accepts a single non-wrapping bbox. Near the dateline use
        // the full longitude span but keep the requested latitude band.
        if (lon_min < -180.0 || lon_max > 180.0) {
            lon_min = -180.0;
            lon_max = 180.0;
        }
        char url[512];
        snprintf(url, sizeof(url),
                 "https://opensky-network.org/api/states/all?lamin=%.5f&lomin=%.5f&lamax=%.5f&lomax=%.5f",
                 lat_min, lon_min, lat_max, lon_max);
        return fetch_states_url(http, url, body, status, headers, win32_error);
    }

private:
    bool fetch_states_url(HttpClient& http, const std::string& url,
                          std::string& body, int& status,
                          std::map<std::string, std::string>& headers,
                          DWORD& win32_error) {
        headers.clear();
        std::string bearer;
        if (!get_token(http, bearer, status, win32_error, &headers)) return false;
        const std::string request_headers =
            "Authorization: Bearer " + bearer + "\r\nAccept: application/json\r\n";
        bool ok = http.request(url, "GET", "", request_headers, body, status,
                               &headers, &win32_error);
        if (status == 401) {
            token_.clear();
            if (get_token(http, bearer, status, win32_error, &headers)) {
                const std::string retry_headers =
                    "Authorization: Bearer " + bearer + "\r\nAccept: application/json\r\n";
                ok = http.request(url, "GET", "", retry_headers, body, status,
                                  &headers, &win32_error);
            }
        }
        return ok;
    }

    bool get_token(HttpClient& http, std::string& token, int& status, DWORD& win32_error,
                   std::map<std::string, std::string>* response_headers) {
        const std::time_t now = std::time(nullptr);
        if (!token_.empty() && now < expires_at_) {
            token = token_;
            return true;
        }
        if (!loaded_) return false;

        const std::string form =
            "grant_type=client_credentials&client_id=" + ltu::opensky::form_url_encode(client_id_)
            + "&client_secret=" + ltu::opensky::form_url_encode(client_secret_);
        const std::string headers =
            "Content-Type: application/x-www-form-urlencoded\r\nAccept: application/json\r\n";
        std::string response;
        if (!http.request("https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
                          "POST", form, headers, response, status,
                          response_headers, &win32_error)) {
            return false;
        }
        long long expires_in = 1800;
        if (!ltu::opensky::json_string_field(response, "access_token", token_)
            || token_.empty()) return false;
        ltu::opensky::json_integer_field(response, "expires_in", expires_in);
        const long long margin = std::min(60LL, std::max(10LL, expires_in / 10));
        expires_at_ = now + static_cast<std::time_t>(std::max(30LL, expires_in - margin));
        token = token_;
        printf("[%s] OpenSky OAuth2: Zugriffstoken erneuert (geheim, nicht protokolliert)\n",
               now_str().c_str());
        return true;
    }

    std::string client_id_;
    std::string client_secret_;
    std::string token_;
    std::time_t expires_at_ = 0;
    bool loaded_ = false;
};

struct Region {
    std::string id;
    double lat = 0, lon = 0;
    int radius = 250;
};

struct Config {
    int interval = 5;             // demand endpoint polling / collector loop interval
    int opensky_interval = 120;   // bounded /states/all: persistent 120-s / daily quota
    std::string url = "http://dnd.59-seconds.com/ltu/index.php";
    std::string token = "CHANGE-ME-BITTE";
    std::string opensky_credentials = "credentials.json"; // nur neben der Collector-EXE
    std::vector<Region> regions;  // optionaler Allowlist-Filter fuer Client-Nachfrage
};

void usage(const char* argv0) {
    printf("Usage: %s   (einfach starten - ganz ohne Argumente)\n"
           "  Reihenfolge: eingebaute Defaults -> ltu_collector.ini neben der exe\n"
           "               -> CLI-Argumente (haben Vorrang)\n"
           "  CLI (alles optional): [--url PROXY_URL] [--token TOKEN] [--interval SEK]\n"
           "          [--region LAT,LON,RADIUS]   (wiederholbar; Allowlist)\n"
           "          [--lat LAT --lon LON [--radius KM]]  (Allowlist-Shortcut)\n"
           "  INI-Keys: url= token= interval= opensky_interval= opensky_credentials= region=\n"
           "  Sammelt nur aktuell angefragte Client-Bereiche; ohne Nachfrage wird pausiert.\n", argv0);
}

bool parse_region(const std::string& s, Region& out, int idx) {
    double la, lo; int ra;
    if (sscanf(s.c_str(), "%lf,%lf,%d", &la, &lo, &ra) == 3) {
        out.lat = la; out.lon = lo;
        out.radius = ltu::adsb::clamp_radius_km(ra);
    } else if (sscanf(s.c_str(), "%lf,%lf", &la, &lo) == 2) {
        out.lat = la; out.lon = lo; out.radius = 250;
    } else {
        return false;
    }
    char id[16];
    snprintf(id, sizeof(id), "R%02d", idx);
    out.id = id;
    return true;
}

// ---------------- Konfig-Datei: ltu_collector.ini neben der exe --------------
//INI-PARSE-START
std::string ini_trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}

// Liest key=wert-Zeilen; ';'/'#' Kommentar, [Sektionen] ignoriert.
// Keys: url, token, interval, opensky_interval, opensky_credentials, region (wiederholbar). Retour: Datei gefunden?
bool read_ini_file(const std::string& path, Config& cfg) {
    std::ifstream f(path.c_str());
    if (!f.is_open()) return false;
    std::string line;
    while (std::getline(f, line)) {
        line = ini_trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string key = ini_trim(line.substr(0, eq));
        std::string val = ini_trim(line.substr(eq + 1));
        for (size_t i = 0; i < key.size(); ++i)
            key[i] = (char)tolower((unsigned char)key[i]);
        if (val.empty()) continue;
        if (key == "url") cfg.url = val;
        else if (key == "token") cfg.token = val;
        else if (key == "interval") cfg.interval = std::max(1, atoi(val.c_str()));
        else if (key == "opensky_interval") cfg.opensky_interval = std::max(120, atoi(val.c_str()));
        else if (key == "opensky_credentials") cfg.opensky_credentials = val;
        else if (key == "region") {
            Region r;
            if (parse_region(val, r, (int)cfg.regions.size())) cfg.regions.push_back(r);
            else printf("INI: ungueltige Region '%s' (Format: LAT,LON,RADIUS)\n", val.c_str());
        }
    }
    return true;
}
//INI-PARSE-ENDE

//INI-PATH-START
std::string executable_directory() {
    char buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameA(nullptr, buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf)) {
        std::string p(buf, n);
        size_t slash = p.find_last_of("\\/");
        if (slash != std::string::npos) return p.substr(0, slash);
    }
    return ".";
}

std::string sibling_path(const std::string& name) {
    return executable_directory() + "\\" + name;
}

std::string ini_path_next_to_exe() {
    return sibling_path("ltu_collector.ini");
}

std::string opensky_credentials_path(const Config& cfg) {
    const std::string& name = cfg.opensky_credentials;
    if (name.find(':') != std::string::npos
        || (!name.empty() && (name[0] == '\\' || name[0] == '/'))) return name;
    return sibling_path(name);
}
//INI-PATH-ENDE

bool parse_args(int argc, char** argv, Config& cfg) {
    double opt_lat = std::nan(""), opt_lon = std::nan("");
    int opt_radius = 250;
    std::vector<Region> cli_regions;   // CLI-Regionen ersetzen INI-Regionen
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char*& v) -> bool {
            if (i + 1 >= argc) return false;
            v = argv[++i];
            return true;
        };
        const char* v = nullptr;
        if (a == "--interval" && next(v)) cfg.interval = std::max(1, atoi(v));
        else if (a == "--url" && next(v)) cfg.url = v;
        else if (a == "--token" && next(v)) cfg.token = v;
        else if (a == "--lat" && next(v)) opt_lat = atof(v);
        else if (a == "--lon" && next(v)) opt_lon = atof(v);
        else if (a == "--radius" && next(v))
            opt_radius = ltu::adsb::clamp_radius_km(atoi(v));
        else if (a == "--region" && next(v)) {
            Region r;
            if (!parse_region(v, r, (int)cli_regions.size())) {
                printf("Ungueltige Region: %s  (Format: LAT,LON,RADIUS)\n", v);
                return false;
            }
            cli_regions.push_back(r);
        }
        else if (a == "--help" || a == "-h") { usage(argv[0]); return false; }
        else { printf("Unbekanntes Argument: %s\n", a.c_str()); usage(argv[0]); return false; }
    }
    if (!cli_regions.empty()) {
        cfg.regions = cli_regions;
    } else if (!std::isnan(opt_lat) && !std::isnan(opt_lon)) {
        // Kompatibilitaet: --lat/--lon/--radius = eine Region (ID R00)
        Region r;
        r.lat = opt_lat; r.lon = opt_lon; r.radius = opt_radius;
        char id[16];
        snprintf(id, sizeof(id), "R%02d", 0);
        r.id = id;
        cfg.regions.clear();
        cfg.regions.push_back(r);
    }
    return true;
}

// Grobe Flugzeugzahl ohne JSON-Parser: Vorkommen von "lat": zaehlen
size_t count_aircraft(const std::string& body) {
    size_t n = 0, pos = 0;
    while ((pos = body.find("\"lat\":", pos)) != std::string::npos) { ++n; pos += 6; }
    return n;
}

struct Stats {
    std::atomic<unsigned long long> push_ok{0}, push_fail{0}, fetch_fail{0};
};

bool push_body(HttpClient& http, const Config& cfg, const std::string& region,
               double lat, double lon, int radius_km, const std::string& body,
               int source_http, const std::string& source_status_region,
               const char* source_mode, bool global_fallback = false,
               const char* source_name = "adsb.lol",
               const char* source_format = "adsb-lol",
               int source_max_age = 0,
               int* proxy_http_out = nullptr,
               DWORD* proxy_win32_out = nullptr) {
    char dst[1024];
    snprintf(dst, sizeof(dst),
             "%s?action=push&region=%s&lat=%.5f&lon=%.5f&radius=%d%s",
             cfg.url.c_str(), region.c_str(), lat, lon, radius_km,
             global_fallback ? "&fallback=global" : "");
    char hdr[1024];
    snprintf(hdr, sizeof(hdr),
             "Content-Type: application/json\r\nX-LTU-Token: %s\r\n"
             "X-LTU-Count: %zu\r\nX-LTU-Source-HTTP: %d\r\n"
             "X-LTU-Source-Status: %s\r\nX-LTU-Source-Mode: %s\r\n"
             "X-LTU-Source-Name: %s\r\nX-LTU-Source-Format: %s\r\n"
             "X-LTU-Source-Max-Age: %d\r\n",
             cfg.token.c_str(), count_aircraft(body), source_http,
             source_status_region.c_str(), source_mode ? source_mode : "",
             source_name ? source_name : "adsb.lol",
             source_format ? source_format : "adsb-lol", source_max_age);
    std::string resp;
    int status = 0;
    DWORD win32_error = 0;
    const bool ok = http.request(dst, "POST", body, hdr, resp, status, nullptr, &win32_error);
    if (proxy_http_out) *proxy_http_out = status;
    if (proxy_win32_out) *proxy_win32_out = win32_error;
    return ok;
}

bool report_source_failure(HttpClient& http, const Config& cfg,
                           const std::string& region, int source_http,
                           const char* source_mode,
                           const char* source_name = "adsb.lol",
                           int source_max_age = 0) {
    char dst[896];
    snprintf(dst, sizeof(dst), "%s?action=source_status&region=%s",
             cfg.url.c_str(), region.c_str());
    char hdr[512];
    snprintf(hdr, sizeof(hdr), "Content-Type: application/json\r\nX-LTU-Token: %s\r\n",
             cfg.token.c_str());
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"http\":%d,\"mode\":\"%s\",\"source\":\"%s\",\"max_age\":%d}",
             source_http, source_mode ? source_mode : "",
             source_name ? source_name : "adsb.lol", source_max_age);
    std::string resp;
    int status = 0;
    return http.request(dst, "POST", payload, hdr, resp, status);
}

int retry_after_seconds(const std::map<std::string, std::string>& headers,
                        int default_seconds);
int retry_delay_seconds(int status,
                        const std::map<std::string, std::string>& headers,
                        unsigned int failures);
void sleep_interruptible(int seconds);

std::string action_url(const std::string& base, const std::string& action) {
    const std::string::size_type query = base.find('?');
    const char* separator = query == std::string::npos ? "?"
                          : ((base.empty() || base[base.size() - 1] == '?' || base[base.size() - 1] == '&') ? "" : "&");
    return base + separator + "action=" + action;
}

bool demand_matches_allowlist(const ltu::demand::Region& demand,
                              const std::vector<Region>& allowlist) {
    if (allowlist.empty()) return true;
    for (std::size_t i = 0; i < allowlist.size(); ++i) {
        const Region& filter = allowlist[i];
        if (!std::isfinite(filter.lat) || !std::isfinite(filter.lon)
            || filter.lat < -90.0 || filter.lat > 90.0
            || filter.lon < -180.0 || filter.lon > 180.0
            || filter.radius < 1 || filter.radius > ltu::adsb::MAX_PUBLIC_RADIUS_KM) continue;
        if (ltu::demand::distance_km(demand.lat, demand.lon, filter.lat, filter.lon)
            <= demand.radius_km + filter.radius) return true;
    }
    return false;
}

bool fetch_client_demands(HttpClient& http, const Config& cfg,
                          std::vector<ltu::demand::Region>& regions,
                          bool& global_search_requested,
                          int& status, DWORD& win32_error,
                          std::size_t& received_count,
                          std::string& error_detail) {
    global_search_requested = false;
    received_count = 0;
    error_detail.clear();
    const std::string url = action_url(cfg.url, "demand");
    const std::string headers = "Accept: application/json\r\nX-LTU-Token: "
                              + cfg.token + "\r\n";
    std::string body;
    const bool request_ok = http.request(url, "GET", "", headers, body, status,
                                         nullptr, &win32_error);
    if (!request_ok && status == 0) {
        error_detail = "WinHTTP request failed";
        return false;
    }
    if (status < 200 || status >= 300) {
        error_detail = "HTTP response status was not successful";
        return false;
    }
    if (!request_ok) {
        error_detail = "WinHTTP response processing failed";
        return false;
    }
    std::vector<ltu::demand::Region> received;
    if (!ltu::demand::parse_regions_json(body, received)) {
        error_detail = "invalid client-demand JSON response";
        return false;
    }
    received_count = received.size();
    global_search_requested = ltu::demand::parse_global_search_requested(body);
    regions.clear();
    for (std::size_t i = 0; i < received.size(); ++i) {
        if (demand_matches_allowlist(received[i], cfg.regions)) regions.push_back(received[i]);
    }
    return true;
}

enum DemandFetchResult {
    DEMAND_SKIPPED,
    DEMAND_SOURCE_FAILED,
    DEMAND_PUSH_FAILED,
    DEMAND_PUSHED
};

struct DemandProviderBackoff {
    std::mutex mutex;
    std::time_t retry_at = 0;
    unsigned int failures = 0;
};

DemandFetchResult fetch_demand_provider(HttpClient& http, const Config& cfg,
                                        const ltu::demand::Region& region,
                                        const char* provider,
                                        DemandProviderBackoff& backoff,
                                        Stats& stats) {
    const std::time_t now = std::time(nullptr);
    if (!g_running || !ltu::demand::is_fresh(region, now)) return DEMAND_SKIPPED;
    {
        std::lock_guard<std::mutex> lock(backoff.mutex);
        if (now < backoff.retry_at) return DEMAND_SKIPPED;
    }
    const std::string url = ltu::adsb::regional_url(
        provider, region.lat, region.lon, region.radius_km);
    std::string body;
    int status = 0;
    DWORD fetch_error = 0;
    std::map<std::string, std::string> headers;
    g_provider_gate.wait(provider);
    // Do not start a queued upstream request if this demand or a feed-wide
    // cooldown expired while waiting for the request gate.
    if (!g_running || !ltu::demand::is_fresh(region, std::time(nullptr))) return DEMAND_SKIPPED;
    {
        std::lock_guard<std::mutex> lock(backoff.mutex);
        if (std::time(nullptr) < backoff.retry_at) return DEMAND_SKIPPED;
    }
    if (http.request(url, "GET", "", "", body, status, &headers, &fetch_error)
        && status >= 200 && status < 300) {
        {
            std::lock_guard<std::mutex> lock(backoff.mutex);
            backoff.failures = 0;
            backoff.retry_at = 0;
        }
        int proxy_status = 0;
        DWORD proxy_error = 0;
        const bool pushed = push_body(http, cfg, region.id, region.lat, region.lon,
                                      region.radius_km, body, status, region.id,
                                      "demand", false, provider, "adsb",
                                      180, &proxy_status, &proxy_error);
        if (pushed) {
            ++stats.push_ok;
            printf("[%s] Client-Bereich %s via %s: %5zu KB -> Proxy OK\n",
                   now_str().c_str(), region.id.c_str(), provider, body.size() / 1024);
            return DEMAND_PUSHED;
        }
        ++stats.push_fail;
        printf("[%s] Client-Bereich %s: Push via %s fehlgeschlagen (Proxy HTTP %d, Win32 %lu)\n",
               now_str().c_str(), region.id.c_str(), provider, proxy_status, proxy_error);
        return DEMAND_PUSH_FAILED;
    }

    ++stats.fetch_fail;
    unsigned int failures = 1;
    int pause = 0;
    {
        std::lock_guard<std::mutex> lock(backoff.mutex);
        failures = ++backoff.failures;
        pause = retry_delay_seconds(status, headers, failures);
        backoff.retry_at = std::time(nullptr) + pause;
    }
    const std::string status_id = region.id + (std::string(provider) == "adsb.fi"
                                               ? "-adsb-fi" : "-adsb-lol");
    report_source_failure(http, cfg, status_id, status, "demand", provider, pause + 30);
    printf("[%s] Client-Bereich %s via %s: Fetch fehlgeschlagen (HTTP %d, Win32 %lu), Retry in %d s\n",
           now_str().c_str(), region.id.c_str(), provider, status, fetch_error, pause);
    return DEMAND_SOURCE_FAILED;
}

void fetch_demand_from_opensky(HttpClient& http, const Config& cfg,
                               const ltu::demand::Region& region,
                               OpenSkyAuth& opensky,
                               ltu::reliability::OpenSkyQuotaGuard& quota,
                               std::time_t& next_probe,
                               std::mutex& opensky_mutex,
                               Stats& stats) {
    std::lock_guard<std::mutex> lock(opensky_mutex);
    if (!opensky.enabled() || !ltu::demand::is_fresh(region, std::time(nullptr))) return;
    const std::time_t now = std::time(nullptr);
    if (now < next_probe) return;
    int quota_wait = 0;
    if (!quota.reserve(now, quota_wait)) {
        next_probe = now + std::max(cfg.opensky_interval,
                                    quota_wait > 0 ? quota_wait : cfg.opensky_interval);
        return;
    }
    next_probe = now + cfg.opensky_interval;
    std::string body;
    int status = 0;
    DWORD fetch_error = 0;
    std::map<std::string, std::string> headers;
    if (opensky.fetch_region(http, region.lat, region.lon, region.radius_km,
                             body, status, headers, fetch_error)
        && status >= 200 && status < 300) {
        int proxy_status = 0;
        DWORD proxy_error = 0;
        if (push_body(http, cfg, region.id, region.lat, region.lon,
                      region.radius_km, body, status, region.id, "demand", false,
                      "opensky", "opensky", 180, &proxy_status, &proxy_error)) {
            ++stats.push_ok;
            printf("[%s] Client-Bereich %s via OpenSky: %5zu KB -> Proxy OK\n",
                   now_str().c_str(), region.id.c_str(), body.size() / 1024);
        } else {
            ++stats.push_fail;
            printf("[%s] Client-Bereich %s: OpenSky-Push fehlgeschlagen (Proxy HTTP %d, Win32 %lu)\n",
                   now_str().c_str(), region.id.c_str(), proxy_status, proxy_error);
        }
        return;
    }
    ++stats.fetch_fail;
    const int pause = status == 429
        ? retry_after_seconds(headers, cfg.opensky_interval)
        : retry_delay_seconds(status, headers, 1);
    next_probe = now + std::max(cfg.opensky_interval, pause);
    report_source_failure(http, cfg, region.id + "-opensky", status,
                          "demand", "opensky", static_cast<int>(next_probe - now) + 30);
    printf("[%s] Client-Bereich %s via OpenSky: Fetch fehlgeschlagen (HTTP %d, Win32 %lu), Retry in %d s\n",
           now_str().c_str(), region.id.c_str(), status, fetch_error,
           static_cast<int>(next_probe - now));
}

void collect_demand_region(HttpClient& http, const Config& cfg,
                           const ltu::demand::Region& region,
                           DemandProviderBackoff& adsblol,
                           DemandProviderBackoff& adsbfi,
                           OpenSkyAuth& opensky,
                           ltu::reliability::OpenSkyQuotaGuard& opensky_quota,
                           std::time_t& next_opensky_probe,
                           std::mutex& opensky_mutex,
                           Stats& stats) {
    DemandFetchResult result = fetch_demand_provider(
        http, cfg, region, "adsb.lol", adsblol, stats);
    if (result == DEMAND_PUSHED || result == DEMAND_PUSH_FAILED
        || !g_running || !ltu::demand::is_fresh(region, std::time(nullptr))) return;

    result = fetch_demand_provider(http, cfg, region, "adsb.fi", adsbfi, stats);
    if (result == DEMAND_PUSHED || result == DEMAND_PUSH_FAILED
        || !g_running || !ltu::demand::is_fresh(region, std::time(nullptr))) return;

    fetch_demand_from_opensky(http, cfg, region, opensky, opensky_quota,
                              next_opensky_probe, opensky_mutex, stats);
}

void fetch_global_search_snapshot(HttpClient& http, const Config& cfg,
                                  OpenSkyAuth& opensky,
                                  ltu::reliability::OpenSkyQuotaGuard& quota,
                                  std::time_t& next_probe, Stats& stats) {
    if (!opensky.enabled()) return;
    const std::time_t now = std::time(nullptr);
    if (now < next_probe) return;

    int quota_wait = 0;
    if (!quota.reserve(now, quota_wait)) {
        next_probe = now + std::max(cfg.opensky_interval,
                                    quota_wait > 0 ? quota_wait : cfg.opensky_interval);
        printf("[%s] Global-OpenSky-Suche wartet auf OAuth-Quota; Retry in %d s\n",
               now_str().c_str(), static_cast<int>(next_probe - now));
        return;
    }

    next_probe = now + cfg.opensky_interval;
    std::string body;
    int status = 0;
    DWORD fetch_error = 0;
    std::map<std::string, std::string> headers;
    if (opensky.fetch_global(http, body, status, headers, fetch_error)
        && status >= 200 && status < 300) {
        int proxy_status = 0;
        DWORD proxy_error = 0;
        const int cache_age = std::max(180, cfg.opensky_interval + 30);
        if (push_body(http, cfg, "global", 0.0, 0.0, 0, body, status,
                      "opensky-global", "global", false, "opensky", "opensky",
                      cache_age, &proxy_status, &proxy_error)
            && proxy_status >= 200 && proxy_status < 300) {
            ++stats.push_ok;
            printf("[%s] Global-OpenSky-Suche: %6zu KB, ~%zu ac -> Proxy OK\n",
                   now_str().c_str(), body.size() / 1024, count_aircraft(body));
            return;
        }

        ++stats.push_fail;
        report_source_failure(http, cfg, "opensky-global", proxy_status,
                              "global", "opensky", cache_age);
        printf("[%s] Global-OpenSky-Suche: Push fehlgeschlagen (Proxy HTTP %d, Win32 %lu)\n",
               now_str().c_str(), proxy_status, proxy_error);
        return;
    }

    ++stats.fetch_fail;
    const int pause = status == 429
        ? retry_after_seconds(headers, cfg.opensky_interval)
        : retry_delay_seconds(status, headers, 1);
    next_probe = now + std::max(cfg.opensky_interval, pause);
    report_source_failure(http, cfg, "opensky-global", status,
                          "global", "opensky",
                          std::max(180, cfg.opensky_interval + 30));
    printf("[%s] Global-OpenSky-Suche: Fetch fehlgeschlagen (HTTP %d, Win32 %lu); Retry in %d s\n",
           now_str().c_str(), status, fetch_error,
           static_cast<int>(next_probe - now));
}

void run_demand(HttpClient& http, const Config& cfg, Stats& stats) {
    OpenSkyAuth opensky;
    const bool opensky_enabled = opensky.load_credentials(opensky_credentials_path(cfg));
    ltu::reliability::OpenSkyQuotaGuard opensky_quota(sibling_path("opensky_quota.txt"));
    std::mutex opensky_mutex;
    printf(" Nachfragegesteuert: Client-Bereiche plus explizit angeforderte globale OpenSky-Suche\n");
    printf(" OpenSky OAuth2: %s (Credentials bleiben ausschliesslich beim Collector)\n",
           opensky_enabled ? "lokal geladen" : "deaktiviert");
    if (!opensky_enabled) {
        report_source_failure(http, cfg, "opensky-global", 0,
                              "global", "opensky",
                              std::max(180, cfg.opensky_interval + 30));
    }
    if (!cfg.regions.empty())
        printf(" Allowlist: %zu konfigurierte Bereichsfilter\n", cfg.regions.size());

    const int demand_poll = std::max(2, std::min(10, cfg.interval));
    const std::size_t worker_limit = 4;
    std::time_t next_poll = 0;
    std::time_t next_opensky_probe = 0;
    std::time_t next_global_opensky_probe = 0;
    bool global_search_requested = false;
    std::vector<ltu::demand::Region> regions;
    std::map<std::string, std::time_t> last_served;
    std::map<std::string, DemandProviderBackoff> provider_backoff;
    bool demand_poll_state_known = false;
    bool last_demand_poll_ok = false;
    std::size_t last_received_regions = 0;
    std::size_t last_allowed_regions = 0;
    bool last_global_search_requested = false;
    int last_demand_http_status = 0;
    DWORD last_demand_win32_error = 0;
    std::string last_demand_error;
    std::time_t last_demand_log = 0;

    while (g_running) {
        const std::time_t now = std::time(nullptr);
        if (now >= next_poll) {
            std::vector<ltu::demand::Region> refreshed;
            int status = 0;
            DWORD proxy_error = 0;
            std::size_t received_count = 0;
            std::string error_detail;
            const bool poll_ok = fetch_client_demands(
                http, cfg, refreshed, global_search_requested, status, proxy_error,
                received_count, error_detail);
            if (poll_ok) {
                regions.swap(refreshed);
                next_poll = now + demand_poll;
            } else {
                regions.clear(); // fail closed: kein gueltiger Demand-Endpunkt -> nichts abrufen
                next_poll = now + demand_poll;
            }

            const std::size_t allowed_count = poll_ok ? regions.size() : 0;
            const bool state_changed = !demand_poll_state_known
                || poll_ok != last_demand_poll_ok
                || (poll_ok && (received_count != last_received_regions
                                || allowed_count != last_allowed_regions
                                || global_search_requested != last_global_search_requested))
                || (!poll_ok && (status != last_demand_http_status
                                 || proxy_error != last_demand_win32_error
                                 || error_detail != last_demand_error));
            if (state_changed || last_demand_log == 0 || now - last_demand_log >= 60) {
                if (poll_ok) {
                    const bool idle = regions.empty() && !global_search_requested;
                    printf("[%s] Demand-Poll OK (HTTP %d): %zu Bereiche erhalten, %zu nach Allowlist; "
                           "globale Suche=%s%s\n",
                           now_str().c_str(), status, received_count, allowed_count,
                           global_search_requested ? "ja" : "nein",
                           idle ? "; Upstream pausiert (keine passende Nachfrage)" : "");
                } else {
                    printf("[%s] Demand-Poll FEHLER: %s (HTTP %d, WinHTTP %lu); "
                           "Upstream bleibt bis zum naechsten Poll pausiert\n",
                           now_str().c_str(), error_detail.c_str(), status, proxy_error);
                }
                last_demand_log = now;
            }
            demand_poll_state_known = true;
            last_demand_poll_ok = poll_ok;
            last_received_regions = poll_ok ? received_count : 0;
            last_allowed_regions = allowed_count;
            last_global_search_requested = global_search_requested;
            last_demand_http_status = status;
            last_demand_win32_error = proxy_error;
            last_demand_error = error_detail;
        }

        if (regions.empty() && !global_search_requested) {
            sleep_interruptible(1);
            continue;
        }

        // Eine kleine Worker-Gruppe bedient die aeltesten Bereiche parallel.
        // Feed-Gates bleiben providerweit aktiv; Demand-TTL wird vor jedem
        // Upstream-Aufruf erneut geprueft, sodass verlassene Bereiche ausfallen.
        std::vector<ltu::demand::Region> batch;
        std::set<std::string> selected_ids;
        while (batch.size() < worker_limit) {
            std::size_t selected = regions.size();
            std::time_t oldest = 0;
            for (std::size_t i = 0; i < regions.size(); ++i) {
                if (selected_ids.count(regions[i].id)
                    || !ltu::demand::is_fresh(regions[i], now)) continue;
                std::map<std::string, std::time_t>::const_iterator it = last_served.find(regions[i].id);
                const std::time_t served = it == last_served.end() ? 0 : it->second;
                if (selected == regions.size() || served < oldest) {
                    selected = i;
                    oldest = served;
                }
            }
            if (selected == regions.size()) break;
            batch.push_back(regions[selected]);
            selected_ids.insert(regions[selected].id);
        }
        if (batch.empty()) {
            if (global_search_requested) {
                fetch_global_search_snapshot(http, cfg, opensky, opensky_quota,
                                             next_global_opensky_probe, stats);
            } else {
                next_poll = 0;
            }
            sleep_interruptible(1);
            continue;
        }

        DemandProviderBackoff& lol = provider_backoff["adsb.lol"];
        DemandProviderBackoff& fi = provider_backoff["adsb.fi"];
        std::vector<std::thread> workers;
        workers.reserve(batch.size());
        for (std::size_t i = 0; i < batch.size(); ++i) {
            const ltu::demand::Region region = batch[i];
            workers.emplace_back([&http, &cfg, region, &lol, &fi, &opensky,
                                  &opensky_quota, &next_opensky_probe,
                                  &opensky_mutex, &stats]() {
                collect_demand_region(http, cfg, region, lol, fi, opensky,
                                      opensky_quota, next_opensky_probe,
                                      opensky_mutex, stats);
            });
        }
        for (std::size_t i = 0; i < workers.size(); ++i) workers[i].join();
        if (global_search_requested) {
            fetch_global_search_snapshot(http, cfg, opensky, opensky_quota,
                                         next_global_opensky_probe, stats);
        }
        const std::time_t served_at = std::time(nullptr);
        for (std::size_t i = 0; i < batch.size(); ++i)
            last_served[batch[i].id] = served_at;
        sleep_interruptible(1); // avoids a hot loop when providers are cooling down

        // Bound the small in-memory scheduler state as clients move.
        if (last_served.size() > 512) {
            const std::time_t cutoff = served_at - 600;
            for (std::map<std::string, std::time_t>::iterator it = last_served.begin();
                 it != last_served.end();) {
                if (it->second < cutoff) last_served.erase(it++);
                else ++it;
            }
        }
    }
}

int retry_after_seconds(const std::map<std::string, std::string>& headers,
                        int default_seconds) {
    const char* names[] = {"X-Rate-Limit-Retry-After-Seconds", "Retry-After"};
    for (std::size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        std::map<std::string, std::string>::const_iterator it = headers.find(names[i]);
        if (it == headers.end()) continue;
        const int parsed = ltu::reliability::retry_after_seconds(
            it->second, std::time(nullptr), -1);
        if (parsed > 0) return parsed;
    }
    return default_seconds;
}

int retry_delay_seconds(int status,
                        const std::map<std::string, std::string>& headers,
                        unsigned int failures) {
    if (status == 429) return retry_after_seconds(headers, 120);
    if (status >= 500 || status == 0) {
        const unsigned int shift = std::min(4U, failures ? failures - 1 : 0U);
        return std::min(600, 30 * (1 << shift));
    }
    return 600;
}

void sleep_interruptible(int seconds) {
    for (int i = 0; i < seconds * 10 && g_running; ++i) Sleep(100);
}


}  // namespace

// Fenster bleibt bei Fehler offen, wenn per Doppelklick gestartet:
static void pause_on_error() {
    printf("\nEnter-Taste druecken zum Schliessen...\n");
    (void)getchar();
}

int main(int argc, char** argv) {
    // Keep console and watchdog-redirected logs visible immediately; otherwise
    // stdout may remain fully buffered when the watchdog captures it to files.
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);
    SetLastError(ERROR_SUCCESS);
    HANDLE single_instance = CreateMutexA(nullptr, TRUE, "Local\\LiveTrafficCollector");
    if (!single_instance) {
        printf("FEHLER: Einmalinstanz-Mutex konnte nicht erstellt werden (Win32 %lu).\n",
               GetLastError());
        return 1;
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        printf("LTU Collector laeuft bereits; keine zweite Instanz gestartet.\n");
        CloseHandle(single_instance);
        return 0;
    }
    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    Config cfg;                                       // 1) eingebaute Defaults
    std::string ini = ini_path_next_to_exe();
    bool has_ini = read_ini_file(ini, cfg);           // 2) ltu_collector.ini neben der exe
    if (!parse_args(argc, argv, cfg)) {               // 3) CLI hat Vorrang
        pause_on_error();
        return 1;
    }

    printf("== LTU Collector (async) ==\n");
    printf(" Konfig   : %s%s\n", ini.c_str(), has_ini ? " (geladen)" : " (nicht gefunden - Defaults)");
    printf(" Proxy    : %s\n", cfg.url.c_str());
    printf(" Token    : %.2s***\n", cfg.token.c_str());
    printf(" Intervall: %d s\n", cfg.interval);
    if (cfg.token == "CHANGE-ME-BITTE") {
        printf(" HINWEIS  : Token ist das Default 'CHANGE-ME-BITTE'.\n"
               "            LTU_TOKEN in index.php muss identisch sein, sonst 401.\n"
               "            Eigenes Token: ltu_collector.ini neben die exe legen.\n");
    }
    printf(" Modus    : Client-Nachfrage (Bereiche werden aus aktuellen Traffic-Reads abgeleitet)\n");
    printf(" Verhalten: ohne Nachfrage >%d s pausieren alle Upstream-Feeds\n",
           ltu::demand::DEFAULT_TTL_SECONDS);
    if (!cfg.regions.empty()) {
        printf(" Allowlist: %zu optionale Regionsfilter (starten selbst keine Abfragen)\n",
               cfg.regions.size());
        for (const Region& r : cfg.regions)
            printf("   %s: %.3f, %.3f  r=%d km\n", r.id.c_str(), r.lat, r.lon, r.radius);
    }
    printf(" Strg+C beendet.\n\n");

    HttpClient http("LTU-Collector/2.0");
    if (!http.valid()) {
        printf("Fehler: WinHttpOpen fehlgeschlagen (%lu)\n", GetLastError());
        pause_on_error();
        return 2;
    }

    Stats stats;
    // Alle Upstream-Fetches laufen durch das Demand-Gate; OpenSky global nur
    // waehrend einer ausdruecklich angeforderten Suche.
    run_demand(http, cfg, stats);

    printf("\nBeendet. Pushes ok=%llu fail=%llu, Fetch-Fehler=%llu\n",
           stats.push_ok.load(), stats.push_fail.load(), stats.fetch_fail.load());
    return 0;
}
