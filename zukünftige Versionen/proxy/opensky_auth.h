#ifndef LTU_OPENSKY_AUTH_H
#define LTU_OPENSKY_AUTH_H

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace ltu {
namespace opensky {

inline void append_utf8(std::string& out, unsigned long cp) {
    if (cp <= 0x7f) {
        out.push_back(static_cast<char>(cp));
    } else if (cp <= 0x7ff) {
        out.push_back(static_cast<char>(0xc0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else if (cp <= 0xffff) {
        out.push_back(static_cast<char>(0xe0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else if (cp <= 0x10ffff) {
        out.push_back(static_cast<char>(0xf0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3f)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    }
}

inline int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

inline bool parse_json_string(const std::string& json, std::size_t& pos,
                              std::string& value) {
    if (pos >= json.size() || json[pos] != '"') return false;
    ++pos;
    value.clear();
    while (pos < json.size()) {
        const unsigned char c = static_cast<unsigned char>(json[pos++]);
        if (c == '"') return true;
        if (c < 0x20) return false;
        if (c != '\\') {
            value.push_back(static_cast<char>(c));
            continue;
        }
        if (pos >= json.size()) return false;
        const char escaped = json[pos++];
        switch (escaped) {
        case '"': value.push_back('"'); break;
        case '\\': value.push_back('\\'); break;
        case '/': value.push_back('/'); break;
        case 'b': value.push_back('\b'); break;
        case 'f': value.push_back('\f'); break;
        case 'n': value.push_back('\n'); break;
        case 'r': value.push_back('\r'); break;
        case 't': value.push_back('\t'); break;
        case 'u': {
            if (pos + 4 > json.size()) return false;
            unsigned long cp = 0;
            for (int i = 0; i < 4; ++i) {
                const int digit = hex_value(json[pos++]);
                if (digit < 0) return false;
                cp = (cp << 4) | static_cast<unsigned long>(digit);
            }
            if (cp >= 0xd800 && cp <= 0xdbff) {
                if (pos + 6 > json.size() || json[pos] != '\\' || json[pos + 1] != 'u')
                    return false;
                pos += 2;
                unsigned long low = 0;
                for (int i = 0; i < 4; ++i) {
                    const int digit = hex_value(json[pos++]);
                    if (digit < 0) return false;
                    low = (low << 4) | static_cast<unsigned long>(digit);
                }
                if (low < 0xdc00 || low > 0xdfff) return false;
                cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
            } else if (cp >= 0xdc00 && cp <= 0xdfff) {
                return false;
            }
            append_utf8(value, cp);
            break;
        }
        default: return false;
        }
    }
    return false;
}

inline bool json_string_field(const std::string& json, const std::string& key,
                              std::string& value) {
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
        if (!parse_json_string(json, cursor, value)) return false;
        return true;
    }
    return false;
}

inline bool json_integer_field(const std::string& json, const std::string& key,
                               long long& value) {
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
        if (cursor >= json.size()) return false;
        char* end = nullptr;
        const long long parsed = std::strtoll(json.c_str() + cursor, &end, 10);
        if (end == json.c_str() + cursor) return false;
        value = parsed;
        return true;
    }
    return false;
}

inline bool read_credentials_json(const std::string& json, std::string& client_id,
                                  std::string& client_secret) {
    const bool have_id = json_string_field(json, "clientId", client_id)
                      || json_string_field(json, "client_id", client_id);
    const bool have_secret = json_string_field(json, "clientSecret", client_secret)
                          || json_string_field(json, "client_secret", client_secret);
    return have_id && have_secret && !client_id.empty() && !client_secret.empty();
}

inline std::string form_url_encode(const std::string& value) {
    static const char hex[] = "0123456789ABCDEF";
    std::string out;
    for (std::size_t i = 0; i < value.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
            || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '*') {
            out.push_back(static_cast<char>(c));
        } else if (c == ' ') {
            out.push_back('+');
        } else {
            out.push_back('%');
            out.push_back(hex[c >> 4]);
            out.push_back(hex[c & 0x0f]);
        }
    }
    return out;
}

} // namespace opensky
} // namespace ltu

#endif
