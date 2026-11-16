#include "ctt/json.hpp"

#include <cmath>
#include <cstdio>

namespace ctt {

std::string Json::escape(std::string_view s) {
    std::string r;
    r.reserve(s.size() + 2);
    r += '"';
    for (char c : s) {
        switch (c) {
            case '"': r += "\\\""; break;
            case '\\': r += "\\\\"; break;
            case '\n': r += "\\n"; break;
            case '\r': r += "\\r"; break;
            case '\t': r += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    r += buf;
                } else {
                    r += c;
                }
        }
    }
    r += '"';
    return r;
}

void Json::add_key(std::string_view key) {
    if (!body_.empty()) body_ += ',';
    body_ += escape(key);
    body_ += ':';
}

Json& Json::add(std::string_view key, const char* v) {
    add_key(key);
    body_ += v ? escape(v) : "null";
    return *this;
}

Json& Json::add(std::string_view key, const std::string& v) {
    add_key(key);
    body_ += escape(v);
    return *this;
}

Json& Json::add(std::string_view key, bool v) {
    add_key(key);
    body_ += v ? "true" : "false";
    return *this;
}

Json& Json::add(std::string_view key, double v) {
    add_key(key);
    if (!std::isfinite(v)) {
        body_ += "null";
    } else {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "%.10g", v);
        body_ += buf;
    }
    return *this;
}

Json& Json::add(std::string_view key, const Json& obj) {
    add_key(key);
    body_ += obj.str();
    return *this;
}

Json& Json::add_raw(std::string_view key, std::string_view raw) {
    add_key(key);
    body_ += raw;
    return *this;
}

}  // namespace ctt
