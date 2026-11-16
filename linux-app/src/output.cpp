#include "ctt/output.hpp"

#include <cstdarg>
#include <cstdio>
#include <mutex>

namespace ctt::out {
namespace {
std::mutex g_mu;
Format g_format = Format::Text;
}  // namespace

void set_format(Format f) { g_format = f; }
Format format() { return g_format; }

void emit(const Json& j, const std::string& text) {
    std::lock_guard<std::mutex> lk(g_mu);
    if (g_format == Format::Json) {
        std::string s = j.str();
        std::fwrite(s.data(), 1, s.size(), stdout);
        std::fputc('\n', stdout);
    } else if (!text.empty()) {
        std::fwrite(text.data(), 1, text.size(), stdout);
        if (text.back() != '\n') std::fputc('\n', stdout);
    }
    std::fflush(stdout);
}

std::string strprintf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int n = std::vsnprintf(nullptr, 0, fmt, ap);
    va_end(ap);
    std::string s(n > 0 ? size_t(n) : 0, '\0');
    if (n > 0) std::vsnprintf(s.data(), s.size() + 1, fmt, ap2);
    va_end(ap2);
    return s;
}

}  // namespace ctt::out
