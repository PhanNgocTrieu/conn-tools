// Minimal JSON object builder (output only).
#pragma once

#include <string>
#include <string_view>
#include <type_traits>

namespace ctt {

class Json {
public:
    Json& add(std::string_view key, const char* v);
    Json& add(std::string_view key, const std::string& v);
    Json& add(std::string_view key, bool v);
    Json& add(std::string_view key, double v);
    Json& add(std::string_view key, const Json& obj);
    Json& add_raw(std::string_view key, std::string_view raw);

    template <typename T,
              std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>, int> = 0>
    Json& add(std::string_view key, T v) {
        add_key(key);
        body_ += std::to_string(v);
        return *this;
    }

    bool empty() const { return body_.empty(); }
    std::string str() const { return "{" + body_ + "}"; }

    static std::string escape(std::string_view s);

private:
    void add_key(std::string_view key);
    std::string body_;
};

}  // namespace ctt
